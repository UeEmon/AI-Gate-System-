"""Upload a trusted main-branch release and wait for SSM's actual exit status."""
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import time
import boto3


def command(bucket, key, digest, revision, region):
    for value, pattern in [(bucket, r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]'),
                           (revision, r'[0-9a-f]{40}'), (digest, r'[0-9a-f]{64}'),
                           (region, r'[a-z0-9-]+')]:
        if not re.fullmatch(pattern, value):
            raise ValueError('Invalid deployment parameter')
    q = shlex.quote
    return '\n'.join([
        'set -eu', 'umask 077', 'stage=$(mktemp -d /opt/ai-gate-stage.XXXXXX)',
        f'aws s3 cp {q("s3://" + bucket + "/" + key)} "$stage/release.tar.gz" --region {q(region)} --only-show-errors',
        f'printf "%s  %s\\n" {q(digest)} "$stage/release.tar.gz" | sha256sum -c -',
        'mkdir "$stage/source"', 'tar -xzf "$stage/release.tar.gz" -C "$stage/source"',
        f'bash "$stage/source/deploy/remote-release.sh" "$stage/source" {q(revision)}',
    ])


def main():
    region = os.environ['AWS_REGION']
    bucket = os.environ['DEPLOY_BUCKET']
    instance = os.environ['INSTANCE_ID']
    revision = os.environ['GITHUB_SHA']
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', instance):
        raise ValueError('Invalid instance ID')
    archive = Path(sys.argv[1])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    key = f'releases/{revision}/{os.environ["GITHUB_RUN_ID"]}-{os.environ["GITHUB_RUN_ATTEMPT"]}.tar.gz'
    script = command(bucket, key, digest, revision, region)
    boto3.client('s3', region_name=region).upload_file(str(archive), bucket, key,
        ExtraArgs={'ServerSideEncryption': 'AES256'})
    ssm = boto3.client('ssm', region_name=region)
    result = ssm.send_command(InstanceIds=[instance], DocumentName='AWS-RunShellScript',
        TimeoutSeconds=120, Parameters={'commands': [script], 'executionTimeout': ['3300']})
    cid = result['Command']['CommandId']
    print(f'SSM command: {cid}', flush=True)
    for _ in range(350):
        try:
            result = ssm.get_command_invocation(CommandId=cid, InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(10)
            continue
        status = result['Status']
        if status == 'Success':
            print(f'Deployed {revision}: container health check passed')
            return
        if status not in ('Pending', 'InProgress', 'Delayed'):
            raise RuntimeError(f'SSM deployment {cid}: {status}; inspect SSM output in AWS')
        time.sleep(10)
    ssm.cancel_command(CommandId=cid, InstanceIds=[instance])
    raise TimeoutError('Deployment timed out; cancellation requested. Inspect EC2 state.')


if __name__ == '__main__':
    main()

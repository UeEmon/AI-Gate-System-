"""SMTP delivery for installations without AWS. SES remains the default."""
from email.message import EmailMessage
from email.utils import make_msgid
import os
import smtplib
import ssl

def send_smtp(subject,body,recipients):
    mode=os.getenv('GATE_SMTP_TLS','starttls')
    if mode not in ('starttls','ssl','none'): raise ValueError('Invalid SMTP TLS mode')
    host=os.environ['GATE_SMTP_HOST']
    port=int(os.getenv('GATE_SMTP_PORT','465' if mode=='ssl' else '587'))
    message=EmailMessage();message['From']=os.environ['GATE_EMAIL_FROM'];message['To']=', '.join(recipients)
    message['Subject']=subject;message['Message-ID']=make_msgid();message.set_content(body)
    context=ssl.create_default_context()
    factory=smtplib.SMTP_SSL if mode=='ssl' else smtplib.SMTP
    options={'timeout':10}
    if mode=='ssl': options['context']=context
    with factory(host,port,**options) as client:
        if mode=='starttls': client.ehlo();client.starttls(context=context);client.ehlo()
        username=os.getenv('GATE_SMTP_USER')
        if username: client.login(username,os.environ['GATE_SMTP_PASSWORD'])
        refused=client.send_message(message)
        if refused: raise smtplib.SMTPRecipientsRefused(refused)
    return {'MessageId':str(message['Message-ID'])}

"""Generate and refresh committed PlantUML sources for the main branch."""
from pathlib import Path
import shutil

import generator


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'docs' / 'generated'
    if output.exists():
        shutil.rmtree(output)
    generator.SOURCE_ROOT = root
    generator.OUTPUT_ROOT = output
    for group in generator.GROUPS:
        # One Pyreverse invocation writes both the classes and packages diagrams.
        generator.source(group, 'classes', force=True)
    print(f'Generated UML sources in {output}')


if __name__ == '__main__':
    main()

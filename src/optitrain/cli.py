"""Small CLI wrapper to expose package entrypoints via -m optitrain.cli

Provides credentials admin helpers used by devs and CI.
"""
from . import main


def run():
    main()


if __name__ == '__main__':
    run()

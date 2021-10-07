#!/usr/bin/python

import sys
from os import environ, path
import argparse
import json

def parse_args():
    parser = argparse.ArgumentParser(usage='Pass to CMake through the EXTRA_KCONFIG_TARGETS variable.')
    parser.add_argument('--outfile', type=str, help='Output file to store the environment variables to.', default='env.json')
    parser.add_argument('root', type=str, help='Kconfig root file')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    data = {
        'root': args.root,
        'env': {}
    }

    for key, val in environ.items():
        if '?' in val:
            val = val.split('?')
        data['env'][key] = val

    with open(args.outfile, 'w') as f:
        json.dump(data, f, indent='\t')
    print(f'Dumped environment variables to {path.realpath(args.outfile)}')

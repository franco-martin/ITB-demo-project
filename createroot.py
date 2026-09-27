#!/usr/bin/env python
import argparse
import utils
import mermaid
import logging
import sys
import os
import utils
import json
import semver
def main():
    from logger import logger
    parser = argparse.ArgumentParser(
                        prog='CreateRoot',
                        description='Lists available images in the index')
    parser.add_argument('--log-level', dest="log_level")
    parser.add_argument('--from', '-f', dest="source", help="The image to use as a source. Like ubuntu:jammy")
    parser.add_argument('--name', '-n', help="The name for the image. Like ubuntu-internal")
    parser.add_argument('--version', '-v', dest='version', help="The initial version to use, we recommend using the default 1.0.0")
    parser.add_argument('--description', '-d', dest='description')
    parser.add_argument('--platforms', help="Comma separated platforms to build the image for, like linux/amd64,linux/arm64. Default: IMAGETREE_PLATFORMS. Stored in index.yml")
    args = parser.parse_args(utils.get_args())
    if args.log_level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', "DEBUG"]:
        logger.setLevel(args.log_level.upper())
        logger.warning("The --log-level argument is deprecated. Use the LOG_LEVEL environment variable instead")

    config = utils.load_config()
    index = utils.load_index()
    if args.version:
        # Validate its semver compatible
        semver.Version.parse(args.version)
        version = args.version
    else:
        version = "1.0.0"
    try:
        platforms = utils.parse_platforms(args.platforms or utils.config['platforms'])
    except ValueError as error:
        logger.critical(error)
        exit(1)
    utils.create_fs(config['image_dir'], args.name, args.source)
    index[args.name]={'children': {}, 'version': version, 'description': args.description}
    if platforms:
        index[args.name]['platforms'] = platforms
    utils.save_index(index)
    mermaid.refresh_tree_file(index)

if __name__ == '__main__':
  main()
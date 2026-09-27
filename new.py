#!/usr/bin/env python
import argparse
import utils
import mermaid
import semver
import os

def main():
    from logger import logger 
    parser = argparse.ArgumentParser(
                        prog='New',
                        description='Adds a new image to the tree',
                        epilog='Something else')
    parser.add_argument('--parent', '-p', dest='parent', help="The parent image to use in the form of ubuntu-jammy/python3-10")
    parser.add_argument('--name', '-n', dest='name', help="The name of the image to use")
    parser.add_argument('--description', '-d', dest='description')
    parser.add_argument('--version', '-v', dest='version', help="The initial version to use, we recommend using the default 1.0.0")
    parser.add_argument('--platforms', help="Comma separated platforms to build the image for, like linux/amd64,linux/arm64. Default: IMAGETREE_PLATFORMS, or the parent's when it isn't set. Stored in index.yml")
    parser.add_argument('--log-level', dest="log_level")
    args = parser.parse_args(utils.get_args())
    if args.log_level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', "DEBUG"]:
        logger.setLevel(args.log_level.upper())
        logger.warning("The --log-level argument is deprecated. Use the LOG_LEVEL environment variable instead")

    logger.info(f"Adding {args.name} as a child of {args.parent}")
    index = utils.load_index()
    config = utils.load_config()
    # Add image
    # Create directory
    if args.version:
        # Validate its semver compatible
        semver.Version.parse(args.version)
        version = args.version
    else:
        version = "1.0.0"
    # Look for the parent in the index
    parent = args.parent
    parent_as_list = parent.split('/')
    parent_as_list.reverse()
    destination = utils.find_image_absolute(index, parent_as_list)
    logger.debug(f"Adding {args.name} to index")
    destination['children'][args.name]={'children': {}, 'version': version, 'description': args.description}
    try:
        platforms = utils.parse_platforms(args.platforms or utils.config['platforms'])
        if platforms:
            destination['children'][args.name]['platforms'] = platforms
        # Fails when the image asks for a platform its parent isn't built for
        utils.get_image_platforms(index, f'{parent}/{args.name}')
    except ValueError as error:
        logger.critical(error)
        if not args.platforms and utils.config['platforms']:
            logger.critical("These platforms come from IMAGETREE_PLATFORMS, pass --platforms with the ones the parent is built for")
        exit(1)
    utils.create_fs(f"{config['image_dir']}/{parent}", args.name, f"{config['registry']['url']}/{parent}:{destination['version']}")          
    utils.save_index(index)
    mermaid.refresh_tree_file(index)

if __name__ == '__main__':
  main()
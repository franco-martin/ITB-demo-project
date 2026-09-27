#!/usr/bin/env python
import argparse
from multiprocessing.sharedctypes import Value
import utils
import mermaid
import os
import json
def main():
    from logger import logger
    parser = argparse.ArgumentParser(
                        prog='Delete',
                        description='Deletes the version of an image')
    parser.add_argument('--target', '-t', dest='target', help="The target image be deleted")
    parser.add_argument('--from-registry', action='store_true', help="Delete the images from the registry")
    parser.add_argument('--all-tags', action='store_true', help="Delete all tags from the registry. Use with --from-registry")
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--log-level', dest="log_level")
    args = parser.parse_args(utils.get_args())
    if args.log_level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', "DEBUG"]:
        logger.setLevel(args.log_level.upper())
        logger.warning("The --log-level argument is deprecated. Use the LOG_LEVEL environment variable instead")

    index = utils.load_index()
    if args.from_registry:
        image = utils.find_image(index, utils.split_path(args.target))
        # Remove the parent from the image, leaving only children
        parent = list(image.keys())[0]
        image = image[parent]
        version = image['version']
        images = [args.target] + utils.child_dict_to_list(image, args.target)
        logger.debug(f"Children list rendered: {images}")
        for image in images:
            print("Deleting image: ", image)
            tags = utils.get_tags_from_registry(image)
            if args.all_tags:
                for tag in tags:
                    print("     Deleting tag from registry: ", tag)
                    utils.delete_image(image, tag, args.dry_run)
            else:
                print("     Deleting tag from registry: ", version)
                utils.delete_image(image, version, args.dry_run)
    try:
        utils.delete_image_from_index(index, utils.split_path(args.target), args.dry_run)
    except ValueError:
        print("ERROR: Image was not found in the index")
        exit(1)
    try:
        utils.delete_image_from_fs(args.target, args.dry_run)
    except FileNotFoundError:
        print("ERROR: Path was not found in the filesystem")
        exit(1)
    if args.dry_run:
        print("Image can be deleted from index and fs, skipped due to --dry-run")
    else:
        print("Image deleted successfully")
    utils.save_index(index)
    mermaid.refresh_tree_file(index, args.dry_run)

if __name__ == '__main__':
  main()
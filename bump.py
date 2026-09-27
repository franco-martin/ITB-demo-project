#!/usr/bin/env python
import argparse
import json
import utils
import mermaid
import json
import os

def main():
    from logger import logger 
    parser = argparse.ArgumentParser(
                        prog='Bump',
                        description='Changes the version of an image',
                        epilog='Something else')
    parser.add_argument('--target', '-t', dest='target', help="The target image to build. In the form of ubuntu-jammy/child/grandchild")
    parser.add_argument('--major', action='store_true', help="Change the major version. e.g. from 1.0.0 to 2.0.0 ")
    parser.add_argument('--minor', action='store_true', help="Change the minor version. e.g. from 1.0.0 to 1.1.0 ")
    parser.add_argument('--patch', action='store_true', help="Change the patch version. e.g. from 1.0.0 to 1.0.1 ")
    parser.add_argument('--patch-children', action='store_true', help="Regardless of the path version change, change the patch version of children e.g. from 1.0.0 to 1.0.1 ")
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--log-level', dest="log_level")
    args = parser.parse_args(utils.get_args())
    if args.log_level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', "DEBUG"]:
        logger.setLevel(args.log_level.upper())
        logger.warning("The --log-level argument is deprecated. Use the LOG_LEVEL environment variable instead")

    if args.dry_run:
        logger.info("DRY RUN IS ACTIVE, changes will not be written to index")
    if args.major:
        logger.info(f"Changing major version of {args.target}")
    if args.minor:
        logger.info(f"Changing minor version of {args.target}")
    if args.patch:
        logger.info(f"Changing patch version of {args.target}")
    index = utils.load_index()
    config = utils.load_config()
    target_as_list = args.target.split('/')
    target_as_list.reverse()
    target_image = utils.find_image_absolute(index, target_as_list)
    # Bump image and children
    utils.bump_children(target_image, args.target, major=args.major, minor=args.minor, patch=args.patch, patch_children=args.patch_children)
    cwd = os.getcwd()
    path = f'{cwd}/{config["image_dir"]}/{args.target}'
    # Get containerfiles
    containerfiles = utils.get_containerfiles(path)
    # The target's own build file is the Containerfile, or the Dockerfile when it has none
    bumped_image_containerfile = f"{path}/{utils.find_build_file_name(path)}"
    logger.debug(f"Skipping from updates since its the target: {bumped_image_containerfile}")
    # Save the containerfile for build
    target_containerfile = containerfiles[containerfiles.index(bumped_image_containerfile)]
    # Remove the containerfile from the image we are bumping
    containerfiles.remove(bumped_image_containerfile)
    logger.debug(f"Containerfiles: {containerfiles}")
    # Removes cwd from path and removes windows' double slashes
    trimmed_containerfiles = []
    for file in containerfiles:
        utils.update_containerfile(index, file, args.dry_run)
        trimmed_containerfiles.append(file.replace(cwd, '').replace("//","/").strip("/"))
    # Add back the target_containerfile for build
    trimmed_containerfiles.append(target_containerfile.replace(cwd, '').replace("//","/").strip("/"))
    builds_file = json.dumps(trimmed_containerfiles, indent=1)
    logger.info(f"This operation will require the following builds\n {builds_file}")

    utils.save_index(index, args.dry_run)
    mermaid.refresh_tree_file(index, args.dry_run)

if __name__ == '__main__':
  main()
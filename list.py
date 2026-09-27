#!/usr/bin/env python
import argparse
import os
import utils
import mermaid
import json
def main():
    from logger import logger
    parser = argparse.ArgumentParser(
                        prog='list',
                        description='Lists available images in the index')
    parser.add_argument('--log-level', dest="log_level")
    parser.add_argument('--format', '-f', choices=['tree', 'json', 'mermaid'], default='tree', help="tree: human readable. json: nested images. mermaid: a flowchart for markdown files. Default: tree")
    parser.add_argument('--json', action='store_true', help="Same as --format json")
    parser.add_argument('--show-versions', action='store_true', help="Include each image's version in the tree. Always on for mermaid unless --no-versions is set")
    parser.add_argument('--no-versions', action='store_true', help="Leave the versions out of the mermaid flowchart")
    parser.add_argument('--no-sources', action='store_true', help="Leave the external images that root images are built from out of the mermaid flowchart")
    parser.add_argument('--direction', choices=['LR', 'RL', 'TD', 'BT'], default='LR', help="The mermaid flowchart direction. Default: LR")
    parser.add_argument('--markdown', action='store_true', help="Print the README block: the fenced mermaid flowchart, a blank line, then the image table")
    parser.add_argument('--link-base', dest="link_base", help="The absolute url that Containerfile links in the mermaid flowchart are relative to. Default: utils.get_link_base()")
    parser.add_argument('--update-file', nargs='?', const='README.md', default=None,
                         help="Replace the text between the '<!-- image-tree:start -->' and '<!-- image-tree:end -->' lines of this markdown file with the README block. Defaults to README.md when no file is given")
    parser.add_argument('--check', action='store_true', help="With --update-file, don't write the file, exit with 1 if it is out of date")
    parser.add_argument('--plan', help="Highlight the images in this plan file from plan.py, e.g. plan.json, in the mermaid flowchart. Implies --format mermaid")
    args = parser.parse_args(utils.get_args())
    if args.log_level in ['CRITICAL', 'ERROR', 'WARNING', 'INFO', "DEBUG"]:
        logger.setLevel(args.log_level.upper())
        logger.warning("The --log-level argument is deprecated. Use the LOG_LEVEL environment variable instead")
    if args.json:
        args.format = 'json'
    if args.update_file:
        args.format = 'mermaid'
    if args.check and not args.update_file:
        logger.critical("--check needs --update-file")
        exit(1)
    highlight = None
    if args.plan:
        if args.update_file:
            logger.critical("--plan can't be used with --update-file, the README shows the tree without a plan")
            exit(1)
        args.format = 'mermaid'
        try:
            with open(args.plan) as plan_file:
                highlight = [image['name'] for image in json.load(plan_file)['images']]
        except FileNotFoundError:
            logger.critical(f"{args.plan} doesn't exist, run python3 ./plan.py first")
            exit(1)

    config = utils.load_config()

    index = utils.load_index()
    if args.format == 'mermaid':
        link_base = args.link_base or utils.get_link_base()
        mermaid_options = dict(direction=args.direction, show_versions=not args.no_versions, show_sources=not args.no_sources, highlight=highlight)
        if args.update_file:
            try:
                changed = mermaid.update_tree_file(index, args.update_file, link_base, check=args.check, **mermaid_options)
            except FileNotFoundError:
                logger.critical(f"{args.update_file} doesn't exist")
                exit(1)
            except ValueError as error:
                logger.critical(error)
                exit(1)
            if args.check:
                if changed:
                    logger.critical(f"The image tree in {args.update_file} is out of date, run list.py --update-file {args.update_file}")
                    exit(1)
                return
            if changed:
                logger.info(f"Updated the image tree in {args.update_file}")
        elif args.markdown:
            print(mermaid.render_readme_block(index, link_base=link_base, **mermaid_options), end='')
        else:
            diagram = mermaid.render_mermaid(index, link_base=link_base, **mermaid_options)
            print(diagram, end='')
        return
    images = utils.list_children(index, show_versions=args.show_versions)
    if args.format == 'json':
        print(json.dumps(images, indent=2))
    else:
        from treelib import Tree
        tree = Tree()
        tree.create_node(".", ".")
        for child in images.keys():
            tree.create_node(child, f'.-{child}', parent=".")
            utils.build_tree(images[child], tree, f'.-{child}')
        tree.show()
if __name__ == '__main__':
  main()

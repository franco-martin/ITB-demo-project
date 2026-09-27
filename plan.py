#!/usr/bin/env python
import argparse
import utils
import os
import json

def format_plan(plan: dict):
    '''
    format_plan(plan) -> '2 image(s) to build:\n  stage 0  reg/ubuntu:1.0.0\n  stage 1  reg/ubuntu/python:1.0.0\n'

    Summarizes a plan for people: the tags that will be built, in build order

    Parameters
    ----------
    plan : dict
        The plan from utils.create_plan

    Returns
    -------
    str
    '''
    if not plan['images']:
        return "Nothing to build\n"
    lines = [f"{len(plan['images'])} image(s) to build:"]
    for image in sorted(plan['images'], key=lambda image: image['stage']):
        platforms = f"  ({', '.join(image['platforms'])})" if image['platforms'] else ''
        lines.append(f"  stage {image['stage']}  {image['tag']}{platforms}")
    return '\n'.join(lines) + '\n'

def main():
    from logger import logger
    parser = argparse.ArgumentParser(
                        prog='Plan',
                        description='Writes the images that need building, and the order to build them in, to a json file for pipeline.py to turn into a CI pipeline, and lists them on stdout')
    parser.add_argument('--target', '-t', dest='target', help="Only plan this image. In the form of ubuntu-jammy/child/grandchild")
    parser.add_argument('--include-children', action='store_true', help="Also plan the target's children. Use with --target")
    parser.add_argument('--all', action='store_true', help="Don't check the registry, plan every image even if its version is already there")
    parser.add_argument('--output', '-o', default='plan.json', help="The file to write the plan to. Default: plan.json")
    args = parser.parse_args(utils.get_args())
    if args.include_children and args.target is None:
        logger.critical("--include-children needs --target")
        exit(1)

    config = utils.load_config()
    if args.target is None:
        containerfiles = utils.get_containerfiles(f'{os.getcwd()}/{config["image_dir"]}')
    else:
        containerfiles = utils.get_containerfiles(f'{os.getcwd()}/{config["image_dir"]}/{args.target}')
        if not args.include_children:
            containerfiles = containerfiles[:1]
    try:
        plan = utils.create_plan(containerfiles, check_registry=not args.all)
    except ValueError as error:
        logger.critical(error)
        exit(1)

    with open(args.output, 'w') as output_file:
        output_file.write(json.dumps(plan, indent=2) + '\n')
    logger.info(f"Wrote the plan for {len(plan['images'])} image(s) to {args.output}")
    print(format_plan(plan), end='')

if __name__ == '__main__':
  main()

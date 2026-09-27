#!/usr/bin/env python
import argparse
import copy
import json
import sys
import yaml
import utils

IMAGE_JOB = '.imagetree-image'
MANIFEST_JOB = '.imagetree-manifest'

class Reference(list):
    '''
    A GitLab '!reference [job, key]' tag, kept as is between the template and the pipeline
    '''
    pass

class TemplateLoader(yaml.SafeLoader):
    pass

class PipelineDumper(yaml.SafeDumper):
    pass

TemplateLoader.add_constructor('!reference', lambda loader, node: Reference(loader.construct_sequence(node)))
PipelineDumper.add_representer(Reference, lambda dumper, data: dumper.represent_sequence('!reference', data, flow_style=True))

def job_name(image_name: str, platform: str=None):
    '''
    job_name('ubuntu/python') -> 'build ubuntu/python'
    job_name('ubuntu/python', 'linux/arm64') -> 'build ubuntu/python linux-arm64'
    '''
    if platform:
        return f'build {image_name} {platform_slug(platform)}'
    return f'build {image_name}'

def platform_slug(platform: str):
    '''
    platform_slug('linux/arm/v7') -> 'linux-arm-v7'
    '''
    return platform.replace('/', '-')

def platform_tag(image: dict, platform: str):
    '''
    platform_tag({'tag': 'reg/ubuntu:1.0.0'}, 'linux/arm64') -> 'reg/ubuntu:1.0.0-linux-arm64'

    The tag a single platform build is pushed to before the manifest job merges them.
    Images without platforms are pushed straight to their tag.
    '''
    if platform:
        return f"{image['tag']}-{platform_slug(platform)}"
    return image['tag']

def image_placeholders(plan: dict, image: dict):
    '''
    Gets the placeholders that every job of an image can use
    '''
    return {
        'NAME': image['name'],
        'VERSION': image['version'],
        'TAG': image['tag'],
        'CONTAINERFILE': image['containerfile'],
        'CONTEXT': image['context'],
        'PARENT': image['parent'] or '',
        'STAGE': str(image['stage']),
        'REGISTRY': plan['registry'],
        'PLATFORMS': ','.join(image['platforms']),
        'PLATFORM_TAGS': ' '.join(platform_tag(image, platform) for platform in image['platforms']),
    }

def platform_placeholders(image: dict, platform: str):
    '''
    Gets the placeholders for one platform build of an image. They're empty, and
    %PLATFORM_TAG% is the image's tag, for images without platforms.
    '''
    parts = platform.split('/') if platform else []
    return {
        'PLATFORM': platform or '',
        'OS': parts[0] if parts else '',
        'ARCH': parts[1] if len(parts) > 1 else '',
        'VARIANT': parts[2] if len(parts) > 2 else '',
        'PLATFORM_TAG': platform_tag(image, platform),
    }

def substitute(value, placeholders: dict):
    '''
    substitute({'script': ['build %TAG%']}, {'TAG': 'reg/ubuntu:1.0.0'}) -> {'script': ['build reg/ubuntu:1.0.0']}

    Replaces every %PLACEHOLDER% in the strings of a job, keys included. Unknown placeholders are left as they are.

    Parameters
    ----------
    value : dict, list or str
        The job, or part of it
    placeholders : dict
        Placeholder names, without the % signs, and their values

    Returns
    -------
    The same structure with the placeholders replaced
    '''
    if isinstance(value, str):
        for name, replacement in placeholders.items():
            value = value.replace(f'%{name}%', replacement)
        return value
    if isinstance(value, Reference):
        return Reference(substitute(item, placeholders) for item in value)
    if isinstance(value, list):
        return [substitute(item, placeholders) for item in value]
    if isinstance(value, dict):
        return {substitute(key, placeholders): substitute(item, placeholders) for key, item in value.items()}
    return value

def make_job(template_job: dict, placeholders: dict, needs: list):
    '''
    Copies a template job, fills in its placeholders and adds needs to the ones it already has
    '''
    job = substitute(copy.deepcopy(template_job), placeholders)
    job['needs'] = list(job.get('needs') or []) + needs
    return job

def render_gitlab(plan: dict, template: dict):
    '''
    Renders the plan as a GitLab child pipeline from a template pipeline.

    The template is a normal pipeline plus two hidden jobs:
    - '.imagetree-image' builds one image, or one platform of a multi-platform image. It becomes
      'build <name>' for images without platforms, and 'build <name> <os>-<arch>' per platform otherwise.
    - '.imagetree-manifest' merges the platform builds of an image into its tag. It becomes
      'build <name>' for images with platforms, and is only needed when the plan has any.
    Every job needs its parent's 'build <parent>' job when the parent is in the plan too.
    Everything else in the template is copied as is.

    Parameters
    ----------
    plan : dict
        The plan from utils.create_plan
    template : dict
        The template pipeline

    Returns
    -------
    str : The pipeline in yaml

    Raises
    ------
    ValueError
        If the template lacks a job that the plan needs
    '''
    if not isinstance(template.get(IMAGE_JOB), dict):
        raise ValueError(f"The template has no '{IMAGE_JOB}' job")
    pipeline = {key: value for key, value in template.items() if key not in (IMAGE_JOB, MANIFEST_JOB)}
    for image in plan['images']:
        placeholders = image_placeholders(plan, image)
        parent_needs = [job_name(parent) for parent in image['depends_on']]
        if not image['platforms']:
            pipeline[job_name(image['name'])] = make_job(template[IMAGE_JOB], placeholders | platform_placeholders(image, None), parent_needs)
            continue
        if not isinstance(template.get(MANIFEST_JOB), dict):
            raise ValueError(f"{image['name']} is built for {placeholders['PLATFORMS']}, and the template has no '{MANIFEST_JOB}' job to merge the platforms")
        for platform in image['platforms']:
            pipeline[job_name(image['name'], platform)] = make_job(template[IMAGE_JOB], placeholders | platform_placeholders(image, platform), parent_needs)
        platform_jobs = [job_name(image['name'], platform) for platform in image['platforms']]
        pipeline[job_name(image['name'])] = make_job(template[MANIFEST_JOB], placeholders, platform_jobs)
    if not plan['images']:
        # GitLab rejects a child pipeline without jobs
        nothing = {'script': ['echo "Every image is already in the registry"']}
        if 'stage' in template[IMAGE_JOB]:
            nothing['stage'] = template[IMAGE_JOB]['stage']
        pipeline['nothing to build'] = nothing
    return yaml.dump(pipeline, Dumper=PipelineDumper, sort_keys=False, width=4096)

def render_github(plan: dict):
    '''
    Renders the plan as GitHub Actions step outputs, to append to $GITHUB_OUTPUT.
    A matrix can't express dependencies between its entries, so there is one matrix per stage:
    - 'stages' holds how many stages there are;
    - 'stage0', 'stage1'... hold {"include": [...]} with one entry per image and platform. Each entry
      has the image's fields plus 'platform', 'os', 'arch', 'variant' and 'platform_tag';
    - 'stage0_manifest'... hold {"include": [...]} with the stage's images that have platforms, plus
      'platform_tags'. It's only written for stages that have such images.
    Stages are always contiguous, so a workflow can skip a stage job when its output is empty.

    Parameters
    ----------
    plan : dict
        The plan from utils.create_plan

    Returns
    -------
    str
    '''
    stages = {}
    for image in plan['images']:
        stages.setdefault(image['stage'], []).append(image)
    lines = [f'stages={len(stages)}']
    for stage in sorted(stages.keys()):
        builds = []
        manifests = []
        for image in stages[stage]:
            for platform in image['platforms'] or [None]:
                entry = image | {key.lower(): value for key, value in platform_placeholders(image, platform).items()}
                builds.append(entry)
            if image['platforms']:
                manifests.append(image | {'platform_tags': image_placeholders(plan, image)['PLATFORM_TAGS']})
        lines.append(f"stage{stage}={json.dumps({'include': builds}, separators=(',', ':'))}")
        if manifests:
            lines.append(f"stage{stage}_manifest={json.dumps({'include': manifests}, separators=(',', ':'))}")
    return '\n'.join(lines) + '\n'

def main():
    from logger import logger
    parser = argparse.ArgumentParser(
                        prog='Pipeline',
                        description='Turns the plan from plan.py into a CI pipeline')
    parser.add_argument('--format', '-f', choices=['gitlab', 'github'], required=True, help="gitlab: a child pipeline rendered from --template. github: step outputs with one matrix per stage")
    parser.add_argument('--plan', '-p', default='plan.json', help="The plan written by plan.py. Default: plan.json")
    parser.add_argument('--template', default='.gitlab/imagetree-pipeline.yml', help=f"The template pipeline for --format gitlab, with the '{IMAGE_JOB}' and '{MANIFEST_JOB}' jobs. Default: .gitlab/imagetree-pipeline.yml")
    parser.add_argument('--output', '-o', help="Write the pipeline to this file instead of stdout")
    args = parser.parse_args(utils.get_args())

    try:
        with open(args.plan) as plan_file:
            plan = json.load(plan_file)
    except FileNotFoundError:
        logger.critical(f"{args.plan} doesn't exist, run python3 ./plan.py first")
        exit(1)

    try:
        if args.format == 'gitlab':
            with open(args.template) as template_file:
                template = yaml.load(template_file, Loader=TemplateLoader)
            output = render_gitlab(plan, template or {})
        else:
            output = render_github(plan)
    except FileNotFoundError:
        logger.critical(f"The template {args.template} doesn't exist")
        exit(1)
    except ValueError as error:
        logger.critical(error)
        exit(1)

    if args.output:
        with open(args.output, 'w') as output_file:
            output_file.write(output)
        logger.info(f"Wrote the {args.format} pipeline for {len(plan['images'])} image(s) to {args.output}")
    else:
        sys.stdout.write(output)

if __name__ == '__main__':
  main()

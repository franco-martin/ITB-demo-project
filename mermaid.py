import os
from utils import config, BUILD_FILE, logger, get_build_file, get_link_base

MERMAID_START_MARKER = '<!-- image-tree:start -->'
MERMAID_END_MARKER = '<!-- image-tree:end -->'

def get_source_image(containerfile: str):
    '''
    get_source_image('images/ubuntu/Containerfile') -> 'docker.io/ubuntu:24.04'

    Gets the image in the first FROM line of a Containerfile

    Parameters
    ----------
    containerfile : str
        The path to the Containerfile

    Returns
    -------
    str
        The image, without flags like --platform or the stage name
    None
        If the Containerfile has no FROM line or doesn't exist
    '''
    try:
        with open(containerfile) as containerfile_file:
            for line in containerfile_file:
                words = line.split()
                if words and words[0].upper() == 'FROM':
                    images = [word for word in words[1:] if not word.startswith('--')]
                    return images[0] if images else None
    except FileNotFoundError:
        logger.warning(f"Could not read {containerfile}")
    return None

def mermaid_label(text: str):
    # Mermaid labels are quoted, so quotes are written as entity codes
    return text.replace('"', '#quot;')

def render_mermaid(index: dict, direction: str='LR', show_versions: bool=True, show_sources: bool=True, highlight: list=None, link_base: str=None):
    '''
    Renders the tree as a mermaid flowchart

    Parameters
    ----------
    index : dict
        The index to render
    direction : str
        The mermaid flowchart direction: LR, RL, TD or BT
    show_versions : bool
        Add each image's version to its node
    show_sources : bool
        Add the external images that root images are built from
    highlight : list(str)
        Image paths to highlight, like the images in a build plan. When set, the rest of the tree is dimmed
    link_base : str
        When set, clicking an image opens its Containerfile at <link_base>/<path>.
        Relative links don't work in mermaid on GitHub, so this must be an absolute url

    Returns
    -------
    str
    '''
    import re
    lines = [f'flowchart {direction}']
    edges = []
    clicks = []
    node_ids = {}

    def node_id(key: str):
        # Ids are derived from the path so the output diffs well when the tree changes
        if key not in node_ids:
            # A prefix keeps ids from clashing with mermaid keywords, like an image named 'end'
            if key.startswith('source:'):
                base = 'src_' + re.sub(r'[^A-Za-z0-9_]', '_', key[len('source:'):])
            else:
                base = 'img_' + re.sub(r'[^A-Za-z0-9_]', '_', key)
            candidate = base
            suffix = 2
            while candidate in node_ids.values():
                candidate = f'{base}_{suffix}'
                suffix += 1
            node_ids[key] = candidate
        return node_ids[key]

    def add_images(images: dict, parent: str):
        for name in images.keys():
            path = f'{parent}/{name}' if parent else name
            label = name
            if show_versions:
                label = f'{name}<br/>{images[name]["version"]}'
            lines.append(f'    {node_id(path)}["{mermaid_label(label)}"]')
            if link_base:
                build_file = get_build_file(path)
                clicks.append(f'    click {node_id(path)} href "{link_base}/{build_file}" "Open {os.path.basename(build_file)}"')
            if parent:
                edges.append(f'    {node_id(parent)} --> {node_id(path)}')
            elif show_sources:
                source = get_source_image(get_build_file(path))
                if source:
                    source_key = f'source:{source}'
                    if source_key not in node_ids:
                        lines.append(f'    {node_id(source_key)}(["{mermaid_label(source)}"])')
                    edges.append(f'    {node_id(source_key)} -.-> {node_id(path)}')
            add_images(images[name]['children'], path)

    add_images(index, '')
    lines += edges
    lines += clicks
    if highlight is not None:
        lines.append('    classDef build fill:#fde68a,stroke:#b45309,color:#1c1917')
        lines.append('    classDef unchanged opacity:0.5')
        images = [key for key in node_ids.keys() if not key.startswith('source:')]
        built = [node_ids[key] for key in images if key in highlight]
        unchanged = [node_ids[key] for key in images if key not in highlight]
        if built:
            lines.append(f'    class {",".join(built)} build')
        if unchanged:
            lines.append(f'    class {",".join(unchanged)} unchanged')
    return '\n'.join(lines) + '\n'

def wrap_mermaid(diagram: str):
    return f'```mermaid\n{diagram}```\n'

def render_image_table(index: dict, link_root: str='.'):
    '''
    Renders every image as a markdown table row with its platforms and links to its Containerfile and context.
    Unlike links in mermaid diagrams, these work on GitLab and GitHub with relative paths.
    Platforms come from index.yml only (the image's own, or its closest ancestor's), not IMAGETREE_PLATFORMS,
    so the table doesn't change with the environment it's rendered in. Images without any say 'default'.

    Parameters
    ----------
    index : dict
        The index to render
    link_root : str
        The directory of the markdown file, links are relative to it

    Returns
    -------
    str
    '''
    def link(path: str):
        return os.path.relpath(path, link_root).replace(os.sep, '/')

    def cell(text):
        return str(text or '').replace('|', '\\|').replace('\n', ' ')

    rows = ['| Image | Version | Platforms | Description | Build file | Context |', '| --- | --- | --- | --- | --- | --- |']
    def add_images(images: dict, parent: str, parent_platforms: list):
        for name in images.keys():
            path = f'{parent}/{name}' if parent else name
            platforms = images[name].get('platforms') or parent_platforms
            build_file = get_build_file(path)
            context = f'{config["image_dir"]}/{path}/context'
            rows.append(f'| {cell(path)} | {cell(images[name]["version"])} | {cell(", ".join(platforms) or "default")} '
                        f'| {cell(images[name].get("description"))} '
                        f'| [{os.path.basename(build_file)}]({link(build_file)}) | [context]({link(context)}) |')
            add_images(images[name]['children'], path, platforms)
    add_images(index, '', [])
    return '\n'.join(rows) + '\n'

def render_readme_block(index: dict, link_root: str='.', link_base: str=None, **mermaid_options):
    '''
    Renders the image tree for a markdown file: the mermaid flowchart followed by the table of images

    Parameters
    ----------
    index : dict
        The index to render
    link_root : str
        The directory of the markdown file, table links are relative to it
    link_base : str
        The absolute url for the flowchart's links, see render_mermaid

    Returns
    -------
    str
    '''
    diagram = render_mermaid(index, link_base=link_base, **mermaid_options)
    return f'{wrap_mermaid(diagram)}\n{render_image_table(index, link_root)}'

def replace_marked_block(content: str, block: str, file: str):
    '''
    Replaces the text between the image tree markers of a file

    Parameters
    ----------
    content : str
        The file's content
    block : str
        The text to put between the markers
    file : str
        The file's name, for the error message

    Returns
    -------
    str : The new content

    Raises
    ------
    ValueError
        If the markers are missing or in the wrong order
    '''
    start = content.find(MERMAID_START_MARKER)
    end = content.find(MERMAID_END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"{file} needs a '{MERMAID_START_MARKER}' line followed by a '{MERMAID_END_MARKER}' line")
    return f'{content[:start + len(MERMAID_START_MARKER)]}\n{block}{content[end:]}'

def update_tree_file(index: dict, path: str, link_base: str=None, check: bool=False, **mermaid_options):
    '''
    Replaces the image tree between the markers of a markdown file with the current one

    Parameters
    ----------
    index : dict
        The index
    path : str
        The markdown file. Table links are relative to its directory
    link_base : str
        The absolute url that the flowchart's links start with, see utils.get_link_base
    check : bool
        Only compare, don't write the file

    Returns
    -------
    bool : Whether the file was out of date

    Raises
    ------
    FileNotFoundError
        If the file doesn't exist
    ValueError
        If the file has no markers
    '''
    block = render_readme_block(index, link_root=os.path.dirname(path) or '.', link_base=link_base, **mermaid_options)
    with open(path) as markdown_file:
        content = markdown_file.read()
    new_content = replace_marked_block(content, block, path)
    if new_content == content:
        return False
    if not check:
        with open(path, 'w') as markdown_file:
            markdown_file.write(new_content)
    return True

def sort_index(index: dict):
    '''
    sort_index({'ubuntu26-04': ..., 'ubuntu24-04': ...}) -> {'ubuntu24-04': ..., 'ubuntu26-04': ...}

    Orders images and their children by name, the way utils.save_index writes index.yml, so a tree
    rendered from an index a script just changed matches the one list.py renders from the file

    Parameters
    ----------
    index : dict
        The index, or an image's children

    Returns
    -------
    dict : A sorted copy
    '''
    return {name: {**image, 'children': sort_index(image.get('children') or {})}
            for name, image in sorted(index.items())}

def refresh_tree_file(index: dict, dry_run: bool=False):
    '''
    Keeps the image tree in IMAGETREE_TREE_FILE (README.md by default) up to date after a script changes the index.
    Does nothing when the setting is empty, or the file or its markers don't exist.

    Parameters
    ----------
    index : dict
        The index, as just saved
    dry_run : bool
        Don't write anything
    '''
    path = config['tree_file']
    if not path or dry_run:
        return
    link_base = get_link_base()
    try:
        changed = update_tree_file(sort_index(index), path, link_base)
    except FileNotFoundError:
        logger.debug(f"{path} doesn't exist, not updating the image tree")
        return
    except ValueError:
        logger.debug(f"{path} has no image tree markers, not updating the image tree")
        return
    if changed:
        logger.info(f"Updated the image tree in {path}")
        if link_base is None:
            logger.warning(f"The image tree in {path} has no links to each image's build file because no link base is set "
                           "and the origin remote couldn't be used, so CI's readme-check will find it out of date. "
                           "Set IMAGETREE_LINK_BASE in .env to the url CI uses, "
                           "like https://gitlab.com/<group>/<project>/-/blob/<default branch>, then run python3 ./list.py --update-file")

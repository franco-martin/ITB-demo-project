from argparse import ArgumentError
from json import JSONDecodeError
import os
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv
from logger import logger
import pip_system_certs.wrapt_requests
import requests
load_dotenv()

# Images are built from a Containerfile. A Dockerfile is used when an image has no Containerfile
BUILD_FILE = 'Containerfile'
LEGACY_BUILD_FILE = 'Dockerfile'

def find_build_file_name(directory: str):
    '''
    find_build_file_name('images/ubuntu') -> 'Containerfile'

    Gets the name of the build file in an image directory: the Containerfile, or the Dockerfile
    when there is no Containerfile

    Parameters
    ----------
    directory : str
        The image directory

    Returns
    -------
    str or None : None when the directory has neither file
    '''
    for name in (BUILD_FILE, LEGACY_BUILD_FILE):
        if os.path.isfile(f'{directory}/{name}'):
            return name
    return None

def load_config():
    config = {
        'registry': {
            'url': os.getenv("IMAGETREE_REGISTRY", "sample_repo"),
            'username': os.getenv("IMAGETREE_REGISTRY_USERNAME"),
            'password': os.getenv("IMAGETREE_REGISTRY_PASSWORD")
        },
        'use_http_registry': os.getenv("IMAGETREE_USE_HTTP_REGISTRY", "False")=="True",
        'image_dir': os.getenv("IMAGETREE_IMAGE_DIR", "images"),
        'image_max_length': int(os.getenv("IMAGETREE_IMAGE_MAX_LENGTH", "80")),
        'platforms': os.getenv("IMAGETREE_PLATFORMS", ""),
        # The markdown file whose image tree createroot/new/bump/delete keep up to date. Empty turns it off
        'tree_file': os.getenv("IMAGETREE_TREE_FILE", "README.md"),
        'custom_command': {
            'delete_tag': os.getenv("IMAGETREE_CUSTOM_DELETE_CMD", None),
            'list_tags': os.getenv("IMAGETREE_CUSTOM_LIST_TAGS_CMD", None),
        }
    }
    return config

config = load_config()

def parse_platforms(value):
    '''
    parse_platforms('linux/amd64, linux/arm64') -> ['linux/amd64', 'linux/arm64']

    Parses a comma separated list of platforms, like the IMAGETREE_PLATFORMS variable
    or the --platforms option

    Parameters
    ----------
    value : str or list(str) or None

    Returns
    -------
    list(str) : Empty when value is empty or None

    Raises
    ------
    ValueError
        If a platform isn't in the os/arch or os/arch/variant form, or is repeated
    '''
    import re
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(',')
    platforms = []
    for platform in value:
        platform = platform.strip()
        if not re.fullmatch(r'[a-z0-9]+/[a-z0-9_]+(/[a-z0-9]+)?', platform):
            raise ValueError(f"Invalid platform '{platform}', use the os/arch form, like linux/amd64 or linux/arm/v7")
        if platform in platforms:
            raise ValueError(f"Platform '{platform}' is listed twice")
        platforms.append(platform)
    return platforms

def get_image_platforms(index: dict, image_path: str):
    '''
    get_image_platforms(index, 'ubuntu/python') -> ['linux/amd64', 'linux/arm64']

    Gets the platforms an image is built for: its own 'platforms' in the index, otherwise
    its closest ancestor's, otherwise IMAGETREE_PLATFORMS. An empty list means a single
    build for the build machine's own platform.

    Parameters
    ----------
    index : dict
        The index
    image_path : str
        The image path in the tree

    Returns
    -------
    list(str)

    Raises
    ------
    ValueError
        If an image asks for a platform that its parent isn't built for
    '''
    platforms = parse_platforms(config['platforms'])
    images = index
    path = []
    for level in split_path(image_path)[::-1]:
        if level not in images:
            raise ValueError(f"Invalid image: {image_path}")
        path.append(level)
        own = parse_platforms(images[level].get('platforms'))
        if own:
            missing = [platform for platform in own if platforms and platform not in platforms]
            if len(path) > 1 and missing:
                raise ValueError(f"{'/'.join(path)} is built for {', '.join(missing)}, which its parent isn't built for")
            platforms = own
        images = images[level]['children']
    return platforms

# Session used for all requests sent to the registry. Credentials and the CA bundle are
# resolved lazily (see get_registry_credentials/get_ca_verify) so nothing runs at import time.
registry_session = requests.Session()
registry_session.headers.update(
    {'accept': 'application/vnd.docker.distribution.manifest.v2+json'}
)

# Credentials are cached per registry host, and bearer tokens per (realm, service, scope),
# so repeated lookups against the same registry don't re-run a credential helper or
# re-request a token.
_registry_credentials_cache = {}
_bearer_token_cache = {}

def get_registry_host():
    '''
    Gets the registry's hostname (with its port, if any), without any namespace.

    Returns
    -------
    str
    '''
    return config['registry']['url'].partition('/')[0]

def get_ca_verify():
    '''
    Gets the value to pass as requests' verify= for the registry and its token service.
    IMAGETREE_REGISTRY_CA_CERT points to a custom CA bundle file; otherwise the default
    CA bundle is used.

    Returns
    -------
    str
        The path to a CA bundle file
    bool
        True, when IMAGETREE_REGISTRY_CA_CERT is not set
    '''
    return os.getenv('IMAGETREE_REGISTRY_CA_CERT') or True

def _credentials_from_auth_entry(entry: dict):
    '''
    Decodes the credentials out of a docker config.json 'auths' entry.

    Parameters
    ----------
    entry : dict
        The matched auths[...] entry

    Returns
    -------
    tuple(str, str)
    None
        If the entry has no usable credentials
    '''
    import base64
    auth = entry.get('auth')
    if auth:
        try:
            decoded = base64.b64decode(auth).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
        username, _, password = decoded.partition(':')
        return (username, password)
    username, password = entry.get('username'), entry.get('password')
    if username and password:
        return (username, password)
    return None

def _match_auth_entry(auths: dict, host: str):
    '''
    Finds the longest matching key in a docker config.json 'auths' mapping, trying
    'host/namespace' (and any shorter prefix of the namespace), then 'host', then
    'https://host'.

    Parameters
    ----------
    auths : dict
        The 'auths' mapping from the auth file
    host : str
        The registry hostname, with its port if any

    Returns
    -------
    tuple(str, dict)
        The matched key and its entry
    tuple(None, None)
        If nothing matched
    '''
    _, _, namespace = config['registry']['url'].partition('/')
    candidates = []
    if namespace:
        parts = namespace.split('/')
        for end in range(len(parts), 0, -1):
            candidates.append(f"{host}/{'/'.join(parts[:end])}")
    candidates.append(host)
    candidates.append(f"https://{host}")
    for candidate in candidates:
        if candidate in auths:
            return candidate, auths[candidate]
    return None, None

def _run_credential_helper(helper: str, host: str):
    '''
    Runs a docker-credential-<helper> 'get' following the credential helper protocol:
    the host is written to its stdin, and it answers with a JSON object on stdout.

    Parameters
    ----------
    helper : str
        The helper's name, without the 'docker-credential-' prefix
    host : str
        The registry hostname, with its port if any

    Returns
    -------
    tuple(str, str)
    None
        If the helper has no credentials for the host

    Raises
    ------
    Exception
        If the helper can't be run or exits with a non-zero status. Its output is
        never included in the message, since it may contain the credentials themselves.
    '''
    import json as json_module
    import subprocess
    command = f'docker-credential-{helper}'
    logger.debug(f"Running the {command} credential helper for {host}")
    try:
        process = subprocess.run(
            [command, 'get'], input=host, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        raise Exception(f"Could not run the {command} credential helper") from None
    if process.returncode != 0:
        raise Exception(f"The {command} credential helper failed for {host}") from None
    try:
        data = json_module.loads(process.stdout)
    except json_module.JSONDecodeError:
        raise Exception(f"The {command} credential helper returned an invalid response") from None
    username, secret = data.get('Username'), data.get('Secret')
    if not username or not secret:
        return None
    return (username, secret)

def _auth_file_path():
    '''
    Finds the auth file to read registry credentials from, per the credentials contract.

    Returns
    -------
    str
    None
        If none of the candidate files exist
    '''
    auth_file = os.getenv('IMAGETREE_REGISTRY_AUTH_FILE')
    if auth_file:
        return auth_file
    xdg_runtime_dir = os.getenv('XDG_RUNTIME_DIR')
    docker_config = os.getenv('DOCKER_CONFIG')
    candidates = [
        os.getenv('REGISTRY_AUTH_FILE'),
        f"{xdg_runtime_dir}/containers/auth.json" if xdg_runtime_dir else None,
        f"{docker_config}/config.json" if docker_config else None,
        os.path.expanduser('~/.docker/config.json'),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None

def _credentials_from_auth_file(host: str):
    '''
    Resolves credentials for host from the auth file, trying credHelpers, then the
    longest auths match, then credsStore.

    Parameters
    ----------
    host : str
        The registry hostname, with its port if any

    Returns
    -------
    tuple(str, str)
    None
        If there is no auth file, or it has no credentials for host
    '''
    import json as json_module
    auth_file = _auth_file_path()
    if not auth_file:
        return None
    logger.debug(f"Reading registry credentials for {host} from {auth_file}")
    with open(auth_file) as stream:
        auth_config = json_module.load(stream)
    cred_helpers = auth_config.get('credHelpers') or {}
    if host in cred_helpers:
        logger.debug(f"Using the '{cred_helpers[host]}' credHelpers entry for {host}")
        return _run_credential_helper(cred_helpers[host], host)
    key, entry = _match_auth_entry(auth_config.get('auths') or {}, host)
    if entry:
        credentials = _credentials_from_auth_entry(entry)
        if credentials:
            logger.debug(f"Using the auths entry '{key}' for {host}")
            return credentials
    creds_store = auth_config.get('credsStore')
    if creds_store:
        logger.debug(f"Using the '{creds_store}' credsStore entry for {host}")
        return _run_credential_helper(creds_store, host)
    return None

def _resolve_registry_credentials(host: str):
    username = os.getenv('IMAGETREE_REGISTRY_USERNAME')
    password = os.getenv('IMAGETREE_REGISTRY_PASSWORD')
    if username and password:
        logger.debug(f"Using registry credentials from the environment for {host}")
        return (username, password)
    if bool(username) != bool(password):
        logger.warning(
            "Only IMAGETREE_REGISTRY_USERNAME or IMAGETREE_REGISTRY_PASSWORD is set, "
            "you need both to authenticate"
        )
    credentials = _credentials_from_auth_file(host)
    if credentials:
        return credentials
    logger.debug(f"No registry credentials found for {host}, using anonymous access")
    return None

def get_registry_credentials(host: str):
    '''
    get_registry_credentials('registry.gitlab.com') -> ('user', 'token')

    Resolves the credentials for a registry host, per the credentials contract:
    the environment, then an auth file's credHelpers/auths/credsStore, then anonymous
    access. Results are cached per host. Only the winning source is ever logged.

    Parameters
    ----------
    host : str
        The registry hostname, with its port if any (no namespace)

    Returns
    -------
    tuple(str, str)
        (username, password) from the winning source
    None
        For anonymous access
    '''
    if host not in _registry_credentials_cache:
        _registry_credentials_cache[host] = _resolve_registry_credentials(host)
    return _registry_credentials_cache[host]

def _configure_registry_session():
    '''
    Lazily applies the resolved credentials and CA setting to registry_session, right
    before it is used. Cheap to call repeatedly: both are cached.
    '''
    host = get_registry_host()
    registry_session.auth = get_registry_credentials(host)
    registry_session.verify = get_ca_verify()

def load_index():
    import yaml
    index = None
    # Read index.yml file into dictionary
    try:
        stream = open('index.yml', 'r')
        index=yaml.safe_load(stream)
        stream.close()
    except:
        open('index.yml', "a").close()
    if not index:
        # If the index is empty
        index = {}
    return index

def get_args():
    import sys
    return sys.argv[1:]

def save_index(index, dry_run=False):
    import yaml
    if not dry_run:
        logger.debug("Saving index to disk")
        index_file = open('index.yml', 'w')
        yaml.dump(index, index_file)
        index_file.close()

def list_children(index: dict, show_versions: bool=False):
    ''' 
    list_children(index, False) -> {'ubi':{'python': None}}
    
    Lists an image's children 
    
    Parameters
    ----------
    path : dict
        The index to use
    
    show_versions : bool
        Include the versions in the name

    Returns
    -------
    dict
    '''
    children = {}
    for key in index.keys():
        name = key
        if index[key]['children'] != {}:
            if show_versions:
                children[f'{name}:{index[key]["version"]}'] = list_children(index[key]['children'], show_versions=show_versions)
            else:
                children[f'{name}'] = list_children(index[key]['children'], show_versions=show_versions)
        else:
            if show_versions:
                children[f'{name}:{index[key]["version"]}'] = None
            else:
                children[f'{name}'] = None
    return children

def child_dict_to_list(index: dict, parent: str):
    ''' 
    child_dict_to_list(find_image(index, ['ubuntu/python']), "ubuntu/python' ) -> ['ubuntu/python/flask', 'ubuntu/python/django']
    
    Transforms child tree into list of images 
    
    Parameters
    ----------
    children : dict
        The children dict to use
    
    parent : str
        The parent to add to the image path
    Returns
    -------
    list
    '''
    list = []
    for name in index['children'].keys():
        list.append(f'{parent}/{name}')
        if index['children'][name]['children'] != {}:
            list = list + child_dict_to_list(index['children'][name], f'{parent}/{name}')
    return list

def create_fs(parent, name, source):
    '''
    Parameters
    ----------
    parent : str
        the parent directory
    name : str
        the name of the image
    source : str
        the source image
    '''
    logger.debug(f"Creating directories for {name}")
    os.mkdir(f'{parent}/{name}')
    os.mkdir(f'{parent}/{name}/context')
    gitkeep = open(f'{parent}/{name}/context/.gitkeep', 'w')
    gitkeep.close()
    logger.debug(f"Creating {BUILD_FILE} for {name} with source {source}")
    containerfile = open(f'{parent}/{name}/{BUILD_FILE}', 'w')
    containerfile.write(f"FROM {source}")
    containerfile.close()

def build_tree(images, tree, parent):
    if images != None:
        for child in images.keys():
            tree.create_node(child, f'{parent}-{child}', parent=parent)
            build_tree(images[child], tree, f'{parent}-{child}')    

def split_path(path):
    ''' 
    split_path('ubuntu/podman') -> ['podman', 'ubuntu']
    
    Returns the path as an inverted list for recursive functions. 
    
    Parameters
    ----------
    path : str
        The path to be split

    Returns
    -------
    list(str)
    '''
    name_as_list = path.split("/")
    name_as_list.reverse()
    return name_as_list

def delete_image_from_fs(path, dry_run=False):
    ''' 
    delete_image_from_fs('ubuntu/podman')
    
    Deletes an image from the filesystem. 
    
    Parameters
    ----------
    path : str
        The path to the target

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If the path does not exist
    '''
    import shutil
    import os
    path = f"{config['image_dir']}/{path}"
    logger.debug(f"Deleting '{path}' from the filesystem")
    os.listdir(path)
    if not dry_run:
        shutil.rmtree(path)

def delete_image_from_index(index, name_as_list, dry_run=False):
    ''' 
    delete_image_from_index(index, ['podman', 'ubuntu'])
    
    Deletes an image from the index
    
    Parameters
    ----------
    index : dict
        The index where to find the image
    name_as_list : list(str)
        The path to the target as an inverted list. ubuntu-jammy/java should be ['java', 'ubuntu-jammy']

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If the image does not exist
    '''
    logger.debug(f"Deleting image with list '{name_as_list}' using index '{index}'")
    if len(name_as_list) == 1:
        if name_as_list[0] in index.keys():
            if not dry_run:
                index.pop(name_as_list[0])
            return
        else:
            raise ValueError(f"Invalid image: {name_as_list}")
    level = name_as_list.pop()
    if level not in index.keys():
        raise ValueError(f"Invalid image: {level}")
    else:
        return delete_image_from_index(index[level]['children'], name_as_list, dry_run)

def find_image(index, name_as_list):
    ''' 
    find_image(index, ['podman', 'ubuntu']) -> {'podman': {'children': {}, 'description': None, 'version': '1.0.0'}}
    
    Finds image in the index
    
    Parameters
    ----------
    index : dict
        The index where to find the image
    name_as_list : list(str)
        The path to the target as an inverted list (use split_path). ubuntu-jammy/java should be ['java', 'ubuntu-jammy']

    Returns
    -------
    dict
        Dictionary with found image

    Raises
    ------
    ValueError
        If the image does not exist
    '''
    logger.debug(f"Finding image with list '{name_as_list}' using index '{index}'")
    if len(name_as_list) == 1:
        if name_as_list[0] in index.keys():
            return {name_as_list[0]: index[name_as_list[0]]}
        else:
            raise ValueError(f"Invalid image: {name_as_list}")
    # Dont use pop since it modifies the actual name_as_list
    level = name_as_list[-1]
    if level not in index.keys():
        raise ValueError(f"Invalid image: {level}")
    else:
        return find_image(index[level]['children'], name_as_list[0:-1])

def find_image_absolute(index, name_as_list):
    '''
    :index the position in the index to look for the image
    :name_as_list The path to the target as an inverted list. ubuntu-jammy/java should be ['java', 'ubuntu-jammy']
    '''
    logger.debug(f"Finding image with list '{name_as_list}' using index '{index}'")
    if len(name_as_list) == 1:
        if name_as_list[0] in index.keys():
            return index[name_as_list[0]]
        else:
            raise ValueError(f"Invalid image: {name_as_list}")
    level = name_as_list.pop()
    if level not in index.keys():
        raise ValueError(f"Invalid image: {level}")
    else:
        return find_image_absolute(index[level]['children'], name_as_list)

def bump_children(image, name,  major=False, minor=False, patch=False, patch_children=False):
    '''
    Bumps an image and its children

    Parameters
    ----------
    image : str
        The image from the index
    name : str
        The name of the image being bumped
    major : bool
        Bump the major version
    minor : bool
        Bump the minor version
    patch : bool
        Bump the patch version
    Returns
    -------
    list(str)
        List of tags if the image is found
    None
        If the image does not exist
    '''
    import semver
    if not major and not minor and not patch:
        raise ArgumentError(None, "No semver level specified")
    if [major, minor, patch].count(True) > 1:
        raise ArgumentError(None, "Multiple semver levels specified, use ONE of major, minor or patch")
    parsed_version = semver.Version.parse(image['version'])
    old = str(parsed_version)
    if major:
        parsed_version = parsed_version.bump_major()
    if minor:
        parsed_version = parsed_version.bump_minor()
    if patch:
        parsed_version = parsed_version.bump_patch()
    logger.info(f"Changed version of {name} from {old} to {str(parsed_version)}")
    image['version'] = str(parsed_version)
    if image['children'] != {}:
        for child in image['children'].keys():
            if patch_children:
                bump_children(image['children'][child], child, major=False, minor=False, patch=True, patch_children=True)
            else:
                bump_children(image['children'][child], child, major=major, minor=minor, patch=patch)

def get_containerfiles(directory):
    '''
    Finds every image's build file under a directory, parents before children.
    Each image uses its Containerfile, or its Dockerfile when it has no Containerfile.

    Parameters
    ----------
    directory : str
        Where to look for

    Returns
    -------
    list(str)
    '''
    logger.debug(f"Finding containerfiles in {directory}")
    contents = os.listdir(f'{directory}')
    containerfiles = []
    build_file = find_build_file_name(directory)
    if build_file is not None:
        if build_file == BUILD_FILE and LEGACY_BUILD_FILE in contents:
            logger.warning(f"{os.path.relpath(directory)} has a {BUILD_FILE} and a {LEGACY_BUILD_FILE}, using the {BUILD_FILE}")
        containerfiles.append(f'{directory}/{build_file}')
    for item in contents:
        if item != "context" and os.path.isdir(f'{directory}/{item}/'):
            nested_containerfiles = get_containerfiles(f'{directory}/{item}/')
            for containerfile in nested_containerfiles:
                containerfiles.append(containerfile.replace("//","/"))
    #Sort the containerfiles by length. An image that depends on image1/child1 will always be longer than its parent.
    sorted_containerfiles = sorted(containerfiles, key=len)
    return sorted_containerfiles

def update_containerfile(index: dict, file: str, dry_run=False):
    import re
    '''
    Parameters
    ----------
    index : dict
        The index where to look for versions
    file : str
        The containerfile to update

    Returns
    -------
    str
        The new version
    '''
    logger.debug(f"Updating containerfile: {file}")
    image_as_path = os.path.dirname(file).replace(f'{os.getcwd()}/{config["image_dir"]}/', "")
    image_path_as_list = split_path(image_as_path)
    # Since we need the parent, we pop the first element
    image_path_as_list.pop(0)
    image_in_index = find_image(index, image_path_as_list)
    # For parents path
    image_path_as_list.reverse()
    parent_path = '/'.join(image_path_as_list)
    version = image_in_index[list(image_in_index.keys())[0]]['version']
    containerfile_content = None
    with open(file, 'r') as containerfile_file:
        containerfile_content = containerfile_file.readlines()
    new_containerfile_content = []
    for line in containerfile_content:
        '''
        Checks for this specific image, the colon is very important since children will still contain the same prefix
        FROM some.registry.com/img1/img2:version
        FROM some.registry.com/img1/img2/img3:version
        If we dont include the colon, both images will match the expression.

        We are also not taking into account images that are not exactly the old version.
        If for some reason someone is using version 1.0.0 and we just updated 1.2.3 to 2.0.0,
        the 1.0.0 version will be updated to 2.0.0.

        We also need to take into account the potential for the 'as' keyword for multi stage build
        FROM ubuntu:jammy as build
        '''
        regex = f"^FROM {config['registry']['url']}/{parent_path}:[0-9]*\\.[0-9]*\\.[0-9]*"
        new_line = line
        if re.match(regex, line):
            new_line = re.sub(regex, f"FROM {config['registry']['url']}/{parent_path}:{version}", line)
        new_containerfile_content.append(new_line)
    if not dry_run:
        with open(file, 'w') as containerfile_file:
                containerfile_file.writelines(new_containerfile_content)
    else:
        logger.info(f"Dry run is active, would have replaced {file}")
    return version

def containerfile_to_image_path(containerfile: str):
    '''
    containerfile_to_image_path('/repo/images/ubuntu/python/Containerfile') -> 'ubuntu/python'

    Gets the image path in the tree from its Containerfile

    Parameters
    ----------
    containerfile : str
        The path to the Containerfile, absolute or relative to the current directory

    Returns
    -------
    str
    '''
    relative = os.path.relpath(os.path.dirname(os.path.abspath(containerfile)), os.path.abspath(config['image_dir']))
    return relative.replace(os.sep, '/')

def create_plan(containerfiles, check_registry=True):
    '''
    create_plan(['images/ubuntu/Containerfile', 'images/ubuntu/python/Containerfile'])
        -> {'registry': 'reg', 'images': [{'name': 'ubuntu', 'stage': 0, ...}, {'name': 'ubuntu/python', 'stage': 1, ...}]}

    Creates the build plan that CI tools use to build images in parallel

    Parameters
    ----------
    containerfiles : list(str)
        The Containerfiles to plan, parents before children (as returned by get_containerfiles)
    check_registry : bool
        Leave out the images whose version is already in the registry

    Returns
    -------
    dict
        Every image has its name, version, tag, containerfile, context, parent, depends_on and stage.
        depends_on only lists the parent when the parent is also in the plan, since a parent that
        is already in the registry doesn't need to wait for anything. stage is 0 for images without
        dependencies and one more than the parent's stage otherwise.
    '''
    index = load_index()
    images = []
    stages = {}
    for containerfile in containerfiles:
        name = containerfile_to_image_path(containerfile)
        version = find_image_absolute(index, split_path(name))['version']
        tag = f'{config["registry"]["url"]}/{name}:{version}'
        if check_registry:
            if find_tag_in_registry(name, version):
                print(f"{tag.ljust(config['image_max_length'])} already in registry", file=sys.stderr)
                continue
            print(f"{tag.ljust(config['image_max_length'])} needs build", file=sys.stderr)
        parent = name.rsplit('/', 1)[0] if '/' in name else None
        depends_on = [parent] if parent in stages else []
        stages[name] = stages[parent] + 1 if depends_on else 0
        relative_dir = os.path.relpath(os.path.dirname(os.path.abspath(containerfile))).replace(os.sep, '/')
        images.append({
            'name': name,
            'version': version,
            'tag': tag,
            'containerfile': f'{relative_dir}/{os.path.basename(containerfile)}',
            'context': f'{relative_dir}/context',
            'parent': parent,
            'depends_on': depends_on,
            'stage': stages[name],
            'platforms': get_image_platforms(index, name),
        })
    return {'registry': config['registry']['url'], 'images': images}

def find_tag_in_registry(image: str, version: str):
    '''
    Parameters
    ----------
    image : str
        The image path in the tree, like ubuntu/python
    version : str
        The tag to look for
    '''
    tags = get_tags_from_registry(image)
    if tags is None:
        return False
    return version in tags

def registry_get(url: str):
    '''
    Sends a GET request to the registry. When the registry asks for a bearer token
    (GitLab, GitHub, Docker Hub and most cloud registries), it gets one from the registry's
    token service, with the configured credentials when they are set, caches it per
    (realm, service, scope), and retries.

    A repository denied to the configured credentials (a 403 from the token service, or a
    401/403 once a token was obtained) is treated the same as "not in the registry": a
    warning is logged and None is returned. A 401 from the token service itself means the
    credentials were rejected outright, and raises.

    Parameters
    ----------
    url : str
        The url to get

    Returns
    -------
    requests.Response
        The registry's response
    None
        If the repository is denied to the configured credentials
    '''
    import re
    _configure_registry_session()
    verify = get_ca_verify()
    response = registry_session.get(url, verify=verify)
    challenge = response.headers.get('WWW-Authenticate', '')
    if response.status_code != 401 or not challenge.lower().startswith('bearer '):
        return response
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.pop('realm', None)
    if not realm:
        return response
    cache_key = (realm, params.get('service'), params.get('scope'))
    token = _bearer_token_cache.get(cache_key)
    if token is None:
        logger.debug(f"Getting a registry token from {realm} for {params}")
        token_response = requests.get(realm, params=params, auth=registry_session.auth, verify=verify)
        if token_response.status_code == 403:
            logger.warning(f"The registry's token service denied access to {params.get('scope')}")
            return None
        if token_response.status_code != 200:
            message = f"The registry's token service responded with status code {token_response.status_code}, check the registry credentials"
            logger.critical(message)
            raise Exception(message)
        body = token_response.json()
        token = body.get('token') or body.get('access_token')
        _bearer_token_cache[cache_key] = token
    response = requests.get(url, headers={
        **registry_session.headers,
        'Authorization': f'Bearer {token}',
    }, verify=verify)
    if response.status_code in (401, 403):
        logger.warning(f"The registry denied access to {url}")
        return None
    return response

def get_tags_from_registry(image :str):
    '''
    Parameters
    ----------
    image : str
        The image that we will get tags from

    Returns
    -------
    list(str)
        List of tags if the image is found
    None
        If the image does not exist
    '''
    logger.debug(f"Getting tags for {image}")
    if config['custom_command']['list_tags']:
        import json
        # Command must format the answer to a list of tags in json format ["v1", "v2"]
        list_command = config['custom_command']['list_tags']
        list_command = list_command\
                            .replace("%REGISTRY%", config['registry']['url'])\
                            .replace("%REPOSITORY%", image)
        logger.debug(f"Using external command to list tags: {list_command}")
        process_output = run_external_command(list_command)
        logger.debug(f"External list tags command '{list_command}' RAW output: '{process_output}'")
        try:
            json_output = json.loads(process_output)
            logger.debug(f"External list tags command '{list_command}' output: '{json_output}'")
            return json_output
        except JSONDecodeError as err:
            print(f"Could not decode json output from external command. \n {process_output}", file=sys.stderr)
            raise err
    else:
        from urllib.parse import urljoin
        url = f"{get_image_url(image)}/tags/list"
        tags = []
        while url:
            response = registry_get(url)
            # registry_get returns None when the repository is denied to the configured
            # credentials; treat that the same as "not in the registry"
            if response is None:
                return None
            # The registry returns 404 for not found images and 200 for found images, everything else is error
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                message = f"The registry responded with an error status code: {response.status_code}"
                logger.critical(message)
                raise Exception(message)
            tags += response.json().get('tags') or []
            # Registries paginate long tag lists with a Link header
            next_page = response.links.get('next', {}).get('url')
            url = urljoin(url, next_page) if next_page else None
        return tags

def get_image_url(image: str):
    '''
    Gets the image url with the registry and protocol
    get_image_url("ubuntu/podman") -> https://registry.com/v2/ubuntu/podman


    Parameters
    ----------
    image : str
        The image to ge the url for
    Returns
    -------
    None
    '''
    # The registry can include a namespace, like registry.gitlab.com/group/project,
    # which goes after /v2/ in the API url
    host, _, namespace = config['registry']['url'].partition('/')
    repository = f"{namespace}/{image}" if namespace else image
    if config['use_http_registry']:
        return f"http://{host}/v2/{repository}"
    return f"https://{host}/v2/{repository}"

def delete_tag(tag: str, dry_run=False):
    '''
    Parameters
    ----------
    tag : str
        The tag url to delete

    Returns
    -------
    None

    APIs documentation:
    Docker https://docs.docker.com/registry/spec/api/#detail
    Opencontainers spec https://github.com/opencontainers/distribution-spec/blob/main/spec.md#deleting-tags
    '''
    logger.info(f"Deleting tag {tag}")
    if not dry_run:
        _configure_registry_session()
        response = registry_session.delete(f"{tag}", verify=get_ca_verify())
        match response.status_code:
            case 202:
                logger.info("Manifest marked for deletion")
            case 400:
                logger.info("Tag deletion is disabled in the registry")
            case 405:
                logger.info("Tag deletion is disabled in the registry")
            case 404:
                logger.info("The image does not exist in the registry")

def delete_manifest(manifest: str, dry_run=False):
    '''
    Parameters
    ----------
    manifest : str
        The url for the manifest to delete

    Returns
    -------
    None

    APIs documentation:
    Docker https://docs.docker.com/registry/spec/api/#detail
    Opencontainers spec https://github.com/opencontainers/distribution-spec/blob/main/spec.md#deleting-tags
    '''
    logger.info(f"Deleting manifest {manifest}")
    if not dry_run:
        _configure_registry_session()
        response = registry_session.delete(f"{manifest}", verify=get_ca_verify())
        match response.status_code:
            case 202:
                logger.info("Manifest marked for deletion")
            case 404:
                logger.info("The repository does not exist in the registry")
            case other:
                raise Exception(f"The registry answered with an invalid status code, {other}")

def delete_image(image, tag, dry_run):
    '''
    Parameters
    ----------
    image : str
        The image that we will delete
    tag : str
        The tag that we will delete

    Returns
    -------
    None

    APIs documentation:
    Docker https://docs.docker.com/registry/spec/api/#detail
    Opencontainers spec https://github.com/opencontainers/distribution-spec/blob/main/spec.md#deleting-tags
    '''
    if config['custom_command']['delete_tag']:
        import subprocess
        delete_command = config['custom_command']['delete_tag']
        delete_command = delete_command\
                            .replace("%REGISTRY%", config['registry']['url'])\
                            .replace("%REPOSITORY%", image)\
                            .replace("%TAG%", tag)
        logger.debug(f"Using external command to delete image: {delete_command}")
        if not dry_run:
            process_output = run_external_command(delete_command)
            logger.debug(f"External delete tags command '{delete_command}' RAW output: '{process_output}'")
    else:
        url = get_image_url(image)
        try:
            digest = get_manifest_for_tag(image, tag)
            manifest_url = f"{url}/manifests/{digest}"
            delete_manifest(manifest_url, dry_run)
        except ValueError:
            logger.info("The manifest is not in the registry")
        tag_url = f"{url}/manifests/{tag}"
        delete_tag(tag_url, dry_run)

def get_manifest_for_tag(image: str, tag: str):
    '''
    Gets the manifest for a tag

    Parameters
    ----------
    image : str
        The image to get the tag for
    tag : str
        The tag that we will delete

    Returns
    -------
    str : Te manifest for the tag

    Raises
    -------
    ValueError : if the registry doesnt answer with 200
    '''
    url = get_image_url(image)
    complete_url = f"{url}/manifests/{tag}"
    logger.info(f"Getting manifest for {url}")
    _configure_registry_session()
    response = registry_session.get(f"{complete_url}", headers={
        "accept": 'application/vnd.docker.distribution.manifest.v2+json'
    }, verify=get_ca_verify())
    if response.status_code == 200:
        return response.json()['config']['digest']
    raise ValueError("The registry's response has an invalid status code: ", response.status_code)

def run_external_command(command: str):
    '''
    Runs an external command

    Parameters
    ----------
    command : str
        The command to run

    Returns
    -------
    str : The output of the command

    Raises
    -------
    Exception : if the command doesnt exit with status 0
    '''
    import subprocess
    logger.debug(f"Running external command '{command}'")
    process = subprocess.run(command.split(" "), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if process.returncode != 0:
        raise Exception(f"External command failed, halting. Output: {process.stdout}")
    return process.stdout.decode('utf-8')

def get_build_file(image_path: str):
    '''
    get_build_file('ubuntu/python') -> 'images/ubuntu/python/Containerfile'

    Gets the file an image is built from: its Containerfile, or its Dockerfile when it has no Containerfile

    Parameters
    ----------
    image_path : str
        The image path in the tree

    Returns
    -------
    str : The path relative to the current directory
    '''
    directory = f'{config["image_dir"]}/{image_path}'
    return f'{directory}/{find_build_file_name(directory) or BUILD_FILE}'

def get_link_base():
    '''
    Gets the url that repository files are linked from, like https://gitlab.com/group/project/-/blob/main.
    IMAGETREE_LINK_BASE takes precedence, then the GitLab CI and GitHub Actions variables, then the
    git origin remote, so local runs match what CI renders.

    Returns
    -------
    str
    None
        Outside CI when IMAGETREE_LINK_BASE is not set and the origin remote can't be used
    '''
    if os.getenv('IMAGETREE_LINK_BASE'):
        return os.getenv('IMAGETREE_LINK_BASE').rstrip('/')
    if os.getenv('CI_PROJECT_URL') and os.getenv('CI_DEFAULT_BRANCH'):
        return f"{os.getenv('CI_PROJECT_URL')}/-/blob/{os.getenv('CI_DEFAULT_BRANCH')}"
    if os.getenv('GITHUB_SERVER_URL') and os.getenv('GITHUB_REPOSITORY'):
        # GitHub resolves HEAD to the default branch
        return f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}/blob/HEAD"
    return get_origin_link_base()

def get_origin_link_base():
    '''
    Gets the link base from the git origin remote, the way CI builds it: <project url>/blob/HEAD
    for github.com, and <project url>/-/blob/<default branch> for any other host, which is assumed
    to be GitLab. The default branch is read from refs/remotes/origin/HEAD, which git sets on clone.

    Returns
    -------
    str
    None
        If there is no origin remote, its url can't be turned into a web url, or the default
        branch isn't known for a GitLab remote
    '''
    project_url = remote_web_url(git_output('remote', 'get-url', 'origin'))
    if not project_url:
        return None
    if urlparse(project_url).hostname == 'github.com':
        link_base = f'{project_url}/blob/HEAD'
    else:
        default_branch = git_output('symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
        if not default_branch or not default_branch.startswith('origin/'):
            logger.debug("The origin remote's default branch isn't known, run git remote set-head origin --auto")
            return None
        link_base = f"{project_url}/-/blob/{default_branch[len('origin/'):]}"
    logger.debug(f"Using {link_base} from the origin remote as the link base")
    return link_base

def remote_web_url(remote: str):
    '''
    remote_web_url('git@gitlab.com:group/project.git') -> 'https://gitlab.com/group/project'

    Turns a git remote url into the project's web url. Credentials are dropped so they never end up
    in a generated file. SSH remotes become https without their port, http(s) remotes keep theirs.

    Parameters
    ----------
    remote : str or None
        The remote url: https://, http://, ssh://, git:// or the scp-like user@host:path form

    Returns
    -------
    str
    None
        If remote is empty, a local path, or has no group/project path
    '''
    import re
    if not remote:
        return None
    remote = remote.strip()
    scp_like = re.fullmatch(r'(?:[^@/]+@)?([^:/]+):(?!//)(.+)', remote)
    if scp_like:
        scheme, host, path = 'https', scp_like.group(1), scp_like.group(2)
    else:
        parsed = urlparse(remote)
        if parsed.scheme not in ('http', 'https', 'ssh', 'git', 'git+ssh') or not parsed.hostname:
            return None
        scheme, host, path = parsed.scheme, parsed.hostname, parsed.path
        if scheme in ('http', 'https'):
            try:
                if parsed.port:
                    host = f'{host}:{parsed.port}'
            except ValueError:
                return None
        else:
            scheme = 'https'
    path = path.strip('/')
    if path.endswith('.git'):
        path = path[:-len('.git')]
    if '/' not in path:
        return None
    return f'{scheme}://{host.lower()}/{path}'

def git_output(*args):
    '''
    git_output('remote', 'get-url', 'origin') -> 'git@gitlab.com:group/project.git'

    Runs a git command in the current directory

    Returns
    -------
    str : Its output without surrounding whitespace
    None
        If git isn't installed or the command fails
    '''
    import subprocess
    try:
        process = subprocess.run(['git', *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    return process.stdout.decode('utf-8').strip() or None


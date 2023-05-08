from pathlib import Path
from sys import stderr
import argparse
import re
import shutil

from nmodl_preprocessor import optimize_nmodl

website = "https://github.com/ctrl-z-9000-times/nmodl_preprocessor"

parser = argparse.ArgumentParser(prog='nmodl_preprocessor',
    description="This program optimizes NMODL files for the NEURON simulator.",
    epilog=f"For more information or to report a problem go to:\n{website}",
    formatter_class=argparse.RawDescriptionHelpFormatter)

parser.add_argument('input_paths', type=str,
        nargs='+',
        help="input filenames and directories")

parser.add_argument('output_directory', type=str)

parser.add_argument('--celsius', type=float, default=None,
        help="temperature of the simulation")

args = parser.parse_args()

# TODO: Environment variable "MODL_INCLUDES"

# Sanity check the input_paths.
input_paths = [Path(path).resolve() for path in args.input_paths]
for path in input_paths:
    assert path.exists(), f'file or directory not found: "{path}"'
# Collect all of the input_files.
extensions  = {'.mod', '.hoc', '.ses', 'py'}
input_files = {} # Dict of file-name -> path
def add_file(x, override=False):
    if x.is_file() and x.suffix in extensions:
        if not override:
            assert x.name not in input_files, f'duplicate file "{x.name}"'
        input_files[x.name] = x
# Add all input directories, check for duplicate project files.
for path in input_paths:
    if path.is_dir():
        for x in path.iterdir():
            add_file(x)
# Scan the current working directory, override existing files.
for path in Path.cwd().iterdir():
    add_file(path, override=True)
# Add all input files, override existing files.
for path in input_paths:
    add_file(path, override=True)

# Sort the input_files by file type.
nmodl_files = []
code_files  = []
copy_files  = []
for path in input_files.values():
    if   path.suffix == '.mod': nmodl_files.append(path)
    elif path.suffix == '.hoc': code_files.append(path)
    elif path.suffix == '.ses': code_files.append(path)
    elif path.suffix == '.py':  code_files.append(path)
    else:
        copy_files.append(path)

# Setup the output directory.
output_directory = Path(args.output_directory).resolve()
if not output_directory.exists():
    assert output_directory.parent.is_dir(), f'directory not found: {output_directory.parent}'
    output_directory.mkdir()
else:
    assert output_directory.is_dir(), "output_directory is not a directory"

# Get all of the words used in each file.
words = {}
word_regex = re.compile(r'\b\w+\b')
for path in code_files:
    with open(path, 'rt') as f:
        text = f.read()
    words[path.name] = {match.group() for match in re.finditer(word_regex, text)}

# Process the NMODL files.
for path in nmodl_files:
    external_symbols = set()
    for file, symbols in words.items():
        if file != path.name:
            external_symbols.update(symbols)
    output_file = output_directory.joinpath(path.name)
    optimize_nmodl.optimize_nmodl(path, output_file, external_symbols, args.celsius)

# Copy over any miscellaneous files from the source directory.
for path in code_files + copy_files:
    print(f'Copy associated file: "{path.name}"')
    dest = output_directory.joinpath(path.name)
    shutil.copy(path, dest)

_placeholder = lambda: None # Symbol for the CLI script to import and call.


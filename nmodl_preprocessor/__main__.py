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

parser.add_argument('project_path', type=str,
        help="input root directory of all simulation files.")

parser.add_argument('output_path', type=str,
        help="output directory for nmodl files.")

parser.add_argument('--celsius', type=float, default=None,
        help="temperature of the simulation")

args = parser.parse_args()

# Setup the output directory.
output_path = Path(args.output_path).resolve()
if not output_path.exists():
    assert output_path.parent.is_dir(), f'directory not found: {output_path.parent}'
    output_path.mkdir()
else:
    assert output_path.is_dir(), "output_path must be a directory"
    # Delete any existing mod files.
    for x in output_path.iterdir():
        if x.name.startswith('_opt_') and x.name.endswith('.mod'):
            x.remove()


# Collect all of the input_files.
project_path = Path(args.project_path).resolve()
assert project_path.exists(), f'file or directory not found: "{project_path}"'
nmodl_files = {} # Dict of file-name -> path
code_files  = {} # Dict of file-name -> path
copy_files  = {} # Dict of file-name -> path
def scan_dir(path):
    for x in path.iterdir():
        if x.is_dir():
            scan_dir(x)
        elif x.is_file():
            assert x.name not in (nmodl_files | code_files), f'duplicate file "{x.name}"'
            if x.suffix == '.mod':
                nmodl_files[x.name] = x
            elif x.suffix in {'.hoc', '.ses', '.py'}:
                code_files[x.name] = x
scan_dir(project_path)

include_dirs = {path.parent for path in nmodl_files.values()}
for path in include_dirs:
    for x in path.iterdir():
        if x.suffix in {'.c', '.cpp', '.h', '.hpp'}:
            copy_files[x.name] = x

# Get all of the words used in the projects source code.
external_symbols = set()
word_regex = re.compile(r'\b\w+\b')
for path in code_files.values():
    with open(path, 'rt') as f:
        text = f.read()
    external_symbols.update(match.group() for match in re.finditer(word_regex, text))

# TODO: Search for assignments to celsius in the code files. Use regex.

# Process the NMODL files.
for path in nmodl_files.values():
    if path.name == 'vecst.mod':
        copy_files.add(path)
        continue
    output_file = output_path.joinpath(f'_opt_{path.name}')
    okay = optimize_nmodl.optimize_nmodl(path, output_file, external_symbols, args.celsius)
    if not okay:
        copy_files.add(path)

# Copy any C/C++ files that might have been included.
for path in copy_files.values():
    shutil.copy(path, output_path.joinpath(path.name))

_placeholder = lambda: None # Symbol for the CLI script to import and call.


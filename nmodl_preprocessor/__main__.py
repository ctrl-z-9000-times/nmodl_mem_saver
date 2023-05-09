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

parser.add_argument('project_dir', type=str,
        help="root directory for all simulation files")

parser.add_argument('model_dir', type=str,
        nargs='?',
        help="input mechanisms directory")

parser.add_argument('output_dir', type=str,
        help="output directory for nmodl files")

parser.add_argument('--celsius', type=float, default=None,
        help="temperature of the simulation")

args = parser.parse_args()

# Setup the output directory.
output_dir = Path(args.output_dir).resolve()
if not output_dir.exists():
    assert output_dir.parent.is_dir(), f'directory not found: {output_dir.parent}'
    output_dir.mkdir()
else:
    assert output_dir.is_dir(), "output_dir is not a directory"
    # Delete any existing mod files.
    for x in output_dir.glob('*.mod'):
        x.unlink()


# 
project_dir = Path(args.project_dir).resolve()
assert project_dir.exists(), f'directory not found: "{project_dir}"'
assert project_dir.is_dir(), "project_dir is not a directory"

# Find all of the mechanism files.
if args.model_dir:
    model_dir = Path(args.model_dir).resolve()
    assert model_dir.exists(), f'directory not found: "{model_dir}"'
    nmodl_files = sorted(model_dir.glob('*.mod'))
else:
    # Recursively search for the model directory.
    stack = [project_dir]
    while path := stack.pop():
        model_dir = path
        if nmodl_files := sorted(model_dir.glob('*.mod')):
            break # Stop searching after finding the model directory.
        else:
            for x in path.iterdir():
                if x.is_dir():
                    stack.push(x)
    else:
        model_dir = None

# Copy any C/C++ files that might have been included into the mechanisms.
copy_files = []
if model_dir:
    copy_files = (
            sorted(model_dir.glob('*.c')) +
            sorted(model_dir.glob('*.h')) +
            sorted(model_dir.glob('*.cpp')) +
            sorted(model_dir.glob('*.hpp')))

# 
hoc_files  = sorted(project_dir.glob("**/*.hoc"))
ses_files  = sorted(project_dir.glob("**/*.ses"))
py_files   = sorted(project_dir.glob("**/*.py"))
code_files = hoc_files + ses_files + py_files

# Get all of the words used in the projects source code.
external_symbols = set()
word_regex = re.compile(br'\b\w+\b')
for path in code_files:
    print(path)
    with open(path, 'rb') as f:
        text = f.read()
    for match in re.finditer(word_regex, text):
        try:
            external_symbols.add(match.group().decode())
        except UnicodeDecodeError:
            pass

# TODO: Search for assignments to celsius in the code files. Use regex.

# Process the NMODL files.
for path in nmodl_files:
    if path.name == 'vecst.mod':
        copy_files.append(path)
        continue
    output_file = output_dir.joinpath(f'_opt_{path.name}')
    okay = optimize_nmodl.optimize_nmodl(path, output_file, external_symbols, args.celsius)
    if not okay:
        copy_files.append(path)

# Copy any C/C++ files that might have been included.
for path in copy_files:
    shutil.copy(path, output_dir.joinpath(path.name))

_placeholder = lambda: None # Symbol for the CLI script to import and call.


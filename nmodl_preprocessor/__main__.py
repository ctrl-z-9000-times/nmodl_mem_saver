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
# Recursively search for the model directory.
elif nmodl_files := sorted(project_dir.glob('**/*.mod')):
    model_dir   = min((path.parent for path in nmodl_files), key=lambda path: len(path.parts))
    nmodl_files = sorted(model_dir.glob('*.mod'))
else:
    model_dir = None

# Copy any C/C++ files that might have been included into the mechanisms.
include_files = []
if model_dir:
    include_files = (
            sorted(model_dir.glob('*.c')) +
            sorted(model_dir.glob('*.h')) +
            sorted(model_dir.glob('*.cpp')) +
            sorted(model_dir.glob('*.hpp')))

# 
hoc_files  = sorted(project_dir.glob("**/*.hoc"))
ses_files  = sorted(project_dir.glob("**/*.ses"))
py_files   = sorted(project_dir.glob("**/*.py"))
code_files = hoc_files + ses_files + py_files

for path in nmodl_files:
    print(f'Mechanism: {path}')

for path in code_files:
    print(f'Source Code: {path}')

for path in include_files:
    print(f'Include C/C++: {path}')

# Search the projects source code.
references = {} # The set of words used in each projects file.
temperatures = set() # Find all assignments to celsius.
word_regex = re.compile(br'\b\w+\b')
float_regex = br'[+-]?((\d+\.?\d*)|(\.\d+))\b([Ee][+-]?\d+)?\b'
celsius_regex = re.compile(br'\bcelsius\s*=\s*' + float_regex)
for path in nmodl_files + code_files + include_files:
    with open(path, 'rb') as f:
        text = f.read()
    if path.suffix in {'.hoc', '.ses'}:
        text = re.sub(br'//.*', b'', text)
    if path.suffix == '.py':
        text = re.sub(br'#.*', b'', text)
    words = set()
    for match in re.finditer(word_regex, text):
        try:
            words.add(match.group().decode())
        except UnicodeDecodeError:
            pass
    references[path] = words
    # Search for assignments to celsius in the code files.
    for match in re.finditer(celsius_regex, text):
        temperatures.add(float(match.group().decode().partition('=')[2]))

# 
external_symbols = set()
for path, words in references.items():
    if path in code_files:
        external_symbols.update(words)

if "celsius" not in external_symbols:
    celsius = 6.3
    print(f'Default temperature: celsius = {celsius}')
elif len(temperatures) == 1:
    celsius = temperatures.pop()
    print(f'Detected temperature: celsius = {celsius}')
elif len(temperatures) > 1:
    celsius = None
    print(f'Detected multiple temperatures:', ', '.join(str(x) for x in temperatures))
else:
    celsius = None
    print(f'Detected temperature but could not read it')


# Process the NMODL files.
for path in nmodl_files:
    if path.name == 'vecst.mod':
        include_files.append(path)
        continue
    # 
    other_nmodl_refs = set()
    for other_nmodl_file in nmodl_files:
        if path != other_nmodl_file:
            other_nmodl_refs.update(references[other_nmodl_file])
    # 
    output_file = output_dir.joinpath(f'_opt_{path.name}')
    okay = optimize_nmodl.optimize_nmodl(path, output_file, external_symbols, other_nmodl_refs, celsius)
    if not okay:
        include_files.append(path)

# Copy any C/C++ files that might have been included.
for path in include_files:
    shutil.copy(path, output_dir.joinpath(path.name))

_placeholder = lambda: None # Symbol for the CLI script to import and call.


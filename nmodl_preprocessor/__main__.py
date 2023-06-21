from pathlib import Path
from sys import stdout, stderr
import argparse
import os
import platform
import re
import shutil
import subprocess

from nmodl_preprocessor import optimize_nmodl

website = "https://github.com/ctrl-z-9000-times/nmodl_preprocessor"

parser = argparse.ArgumentParser(prog='nmodl_preprocessor',
    description="This program optimizes NMODL files for the NEURON simulator.",
    epilog=f"For more information or to report a problem go to:\n{website}",
    formatter_class=argparse.RawDescriptionHelpFormatter)

parser.add_argument('project_dir', type=str,
        help="root directory of all simulation files")

parser.add_argument('model_dir', type=str,
        nargs='*',
        help="input directory of nmodl files")

args = parser.parse_args()


# 
project_dir = Path(args.project_dir).resolve()
assert project_dir.exists(), f'directory not found: "{project_dir}"'
assert project_dir.is_dir(), "project_dir is not a directory"

# Find all of the mechanism files.
# Check the command line arguments.
args.model_dir = [x for x in args.model_dir if x.strip()]
if args.model_dir:
    model_dir = [Path(x).resolve() for x in args.model_dir]
    nmodl_files = []
    for path in model_dir:
        assert path.exists(), f'directory not found: "{path}"'
        nmodl_files.extend(path.glob('*.mod'))
    nmodl_files.sort()
# Use the project_dir by default.
elif nmodl_files := sorted(project_dir.glob('*.mod')):
    model_dir = [project_dir]
# Recursively search for the model directory.
elif nmodl_files := list(project_dir.glob('**/*.mod')):
    model_dir = sorted(set(path.parent for path in nmodl_files))
    assert len(model_dir) == 1, "Multiple nmodl directories found"
    nmodl_files = sorted(model_dir[0].glob('*.mod'))
# Quietly do nothing.
else:
    model_dir = []
    nmodl_files = []


# Setup the output directory.
output_dir = project_dir.joinpath('.preprocessed')
if not output_dir.exists():
    output_dir.mkdir()
else:
    assert output_dir.is_dir(), "output_dir is not a directory"
    assert output_dir != model_dir, "operation would overwrite its inputs"
    # Delete any existing mod files.
    for x in output_dir.glob('*.mod'):
        x.unlink()

# Copy any C/C++ files that might have been included into the mechanisms.
include_files = []
for path in model_dir:
    include_files.extend(path.glob('*.c'))
    include_files.extend(path.glob('*.h'))
    include_files.extend(path.glob('*.cpp'))
    include_files.extend(path.glob('*.hpp'))
    include_files.extend(path.glob('*.inc'))
include_files.sort()

# 
hoc_files  = sorted(project_dir.glob("**/*.hoc")) + sorted(project_dir.glob("**/*.oc"))
ses_files  = sorted(project_dir.glob("**/*.ses"))
py_files   = sorted(project_dir.glob("**/*.py"))
code_files = hoc_files + ses_files + py_files

ignore_misc = {".mod", ".o", ".pyc", '.png', '.jpg', '.html', '.md', '.pdf'}
misc_files  = set(project_dir.glob("**/*"))
misc_files  = {x for x in misc_files if x.is_file()}
misc_files  = {x for x in misc_files if not any(c.startswith('.') for c in x.parts)}
misc_files  = {x for x in misc_files if not any(c.startswith('__') for c in x.parts)}
misc_files  = {x for x in misc_files if not any(c == platform.machine() for c in x.parts)}
misc_files  = {x for x in misc_files if x.suffix not in ignore_misc}
misc_files -= set(nmodl_files)
misc_files -= set(code_files)
misc_files -= set(include_files)
misc_files  = sorted(misc_files)

# Search the projects source code.
references = {} # The set of words used in each projects file.
word_regex = re.compile(br'\b\w+\b')
temperatures = set() # Find all assignments to celsius.
float_regex = br'[+-]?((\d+\.?\d*)|(\.\d+))\b([Ee][+-]?\d+)?\b'
celsius_regex = re.compile(br'\bcelsius\s*=\s*' + float_regex)
for path in (nmodl_files + code_files + include_files + misc_files):
    references[path] = words = set()
    try:
        with open(path, 'rb') as f:
            text = f.read()
    except OSError:
        if path.suffix == '.mod':
            raise
        else:
            if path in include_files: include_files.remove(path)
            if path in code_files:    code_files.remove(path)
            if path in misc_files:    misc_files.remove(path)
            continue
    # Ignore binary files (anything that's not valid utf-8).
    if path in misc_files:
        try:
            text.decode()
        except UnicodeDecodeError:
            misc_files.remove(path)
            continue
    # Remove line comments.
    # TODO: Also remove multi-line comments.
    if path.suffix in ['.hoc', '.oc', '.ses', '.h', '.c', '.hpp', '.cpp']:
        text = re.sub(br'//.*', b'', text)
    elif path.suffix in ['.py']:
        text = re.sub(br'#.*', b'', text)
    elif path.suffix in ['.mod', '.inc']:
        text = re.sub(br':.*', b'', text)
    # TODO: Special cases for NMODL VERBATIM statements, which can access more
    # symbols than regular NMODL code.
    pass
    # Scan for words.
    for match in re.finditer(word_regex, text):
        try:
            words.add(match.group().decode())
        except UnicodeDecodeError:
            pass
    # Search for assignments to celsius in the code files.
    if path.suffix in {'.hoc', '.ses', '.py'}:
        for match in re.finditer(celsius_regex, text):
            temperatures.add(float(match.group().decode().partition('=')[2]))

print(f"Project Directory: {project_dir}")
for x in model_dir:
    print(f"Model Directory: {x}")
print(f"Output Directory: {output_dir}")

for path in nmodl_files:
    print(f'Mechanism: {path}')

for path in include_files:
    print(f'Include: {path}')

for path in code_files:
    print(f'Source Code: {path}')

for path in misc_files:
    print(f'Misc File: {path}')

stdout.flush()
stderr.flush()

# 
external_symbols = set()
for path in (code_files + include_files + misc_files):
    external_symbols.update(references[path])

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

# Copy any C/C++ files that might have been included.
for path in include_files:
    shutil.copy(path, output_dir.joinpath(path.name))

# Process the NMODL files.
for path in nmodl_files:
    if path.name in {'vecst.mod', 'stats.mod'}:
        shutil.copy(path, output_dir.joinpath(path.name))
        continue
    # 
    other_nmodl_refs = set()
    for other_nmodl_file in (nmodl_files + include_files):
        if other_nmodl_file.suffix in {'.mod', '.inc'}:
            if path != other_nmodl_file:
                other_nmodl_refs.update(references[other_nmodl_file])
    # 
    output_file = output_dir.joinpath(path.name)
    optimize_nmodl.optimize_nmodl(path, output_file, external_symbols, other_nmodl_refs, celsius)

    stdout.flush()
    stderr.flush()

# Compile the NMODL files into the special linked library using nrnivmodl.
env = os.environ
env["MAKEFLAGS"] = " --max-load 0.0"
subprocess.run(["nrnivmodl"] + [str(x) for x in output_dir.glob("*.mod")],
        cwd=project_dir,
        env=env,
        check=True,)

os.sync()

_placeholder = lambda: None # Symbol for the CLI script to import and call.


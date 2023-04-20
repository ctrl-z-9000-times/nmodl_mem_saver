# nmodl_preprocessor

This program optimizes NMODL files for the NEURON simulator.  
It performs the following optimizations to ".mod" files:  
* Hardcode the parameters
* Hardcode the temperature
* Hardcode any assigned variables with constant values
* Inline all functions and procedures
* Convert assigned variables into local variables

These optimizations can improve run-time performance and memory usage by as much
as 15%.

## Installation

#### Prerequisites
* [Python](https://www.python.org/) and [pip](https://pip.pypa.io/en/stable/)
* [The NMODL Framework](https://bluebrain.github.io/nmodl/html/index.html)

```
pip install nmodl_preprocessor
```

## Usage
```
$ nmodl_preprocessor [-h] [--celsius CELSIUS] input_path output_path

positional arguments:
  input_path         input filename or directory of nmodl files
  output_path        output filename or directory for nmodl files

options:
  -h, --help         show this help message and exit
  --celsius CELSIUS  temperature of the simulation

```

## Tips

* This program will not optimize any RANGE or GLOBAL symbols.  
  - Remove them unless you actually need to inspect or modify
    their value at run-time.  
  - Add parameters to a GLOBAL statement to preserve them.  

* Remove unnecessary VERBATIM statements.  


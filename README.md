# nmodl_preprocessor

This program performs the following optimizations to ".mod" files:
* Inline parameters
* Inline temperature
* Inline functions and procedures
* Inline assigned variables with constant values
* Convert assigned variables into local variables

These optimizations can improve runtime performance by as much as 15%.

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

* Remove variables from RANGE and GLOBAL statements unless you actually need to
inspect or modify their value.  

* Remove unnecessary VERBATIM statements.  


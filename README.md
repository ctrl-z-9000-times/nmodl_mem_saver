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
* [The NMODL Framework](https://bluebrain.github.io/nmodl/html/index.html)
* [The NEURON simulator](https://www.neuron.yale.edu/neuron/)
* Python and pip

```
git clone https://github.com/ctrl-z-9000-times/nmodl_preprocessor.git
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
  --celsius CELSIUS

```

## Tips

* Remove variables from RANGE and GLOBAL statements unless you actually need to
inspect or modify their value.  

* Remove unnecessary VERBATIM statements.  


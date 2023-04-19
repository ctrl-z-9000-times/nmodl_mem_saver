import nmodl.dsl
from nmodl_preprocessor.utils import *

class RW_Visitor(nmodl.dsl.visitor.AstVisitor):
    """ Determines which symbols each top-level block reads from and writes to. """
    def __init__(self):
        super().__init__()
        self.current_block = None
        # Maps from block name to set of symbol names.
        self.reads = {}
        self.writes = {}
        # Set of all variables which are assigned to.
        self.all_writes = set()

    def visit_program(self, node):
        node.visit_children(self)
        for block_name, var_names in self.writes.items():
            self.all_writes.update(var_names)

    def visit_statement_block(self, node):
        # Look for top level code blocks.
        if self.current_block is None:
            self.current_block = get_block_name(node.parent)
            self.reads[self.current_block]  = set()
            self.writes[self.current_block] = set()
            node.visit_children(self)
            self.current_block = None
        else:
            node.visit_children(self)

    def visit_binary_expression(self, node):
        if node.op.eval() == '=':
            # Recursively mark all variables on right hand side as being read from.
            node.rhs.accept(self)
            # Mark the left hand side variable of this assignment as being written to.
            name = STR(node.lhs.name.get_node_name())
            self.writes[self.current_block].add(name)
        else:
            node.visit_children(self)

    def visit_var_name(self, node):
        # Mark this variable as being read from, unless its already been
        # overwritten with a new value in this block.
        name = STR(node.name.get_node_name())
        if name not in self.writes[self.current_block]:
            self.reads[self.current_block].add(name)

    def visit_if_statement(self, node):
        node.condition.accept(self)
        # Collect all of the child blocks that are part of this if-else tree.
        blocks = [node.get_statement_block()] + node.elseifs + [node.elses]
        visitors = []
        for x in blocks:
            if x is None: continue
            # Make a new visitor and give it our current state.
            visitors.append(inner := RW_Visitor())
            inner.current_block              = self.current_block
            inner.reads[self.current_block]  = set(self.reads[self.current_block])
            inner.writes[self.current_block] = set(self.writes[self.current_block])
            inner.visit_statement_block(x)
        # Apply the results of the child visitors simultaneously.
        for inner in visitors:
            self.reads[self.current_block].update(inner.reads[self.current_block])
            self.writes[self.current_block].update(inner.writes[self.current_block])

    def visit_neuron_block(self, node):
        pass # Does not contain any source code.

    def visit_function_block(self, node):
        pass # Functions are pure and can't access assigned variables.


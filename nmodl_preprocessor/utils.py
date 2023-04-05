STR = lambda x: str(x).strip() # nmodl sometimes leaves trailing whitespace on stuff.

def get_block_name(node):
    if name := getattr(node, 'name', None):
        return STR(name)
    try:
        return STR(node.get_nmodl_name())
    except RuntimeError:
        return STR(node.get_node_type_name())


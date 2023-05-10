STR = lambda x: str(x).strip() # nmodl sometimes leaves trailing whitespace on stuff.

def get_block_name(node):
    if node.is_ba_block():
        return get_block_name(node.parent)
    if node.is_before_block():
        return 'BEFORE ' + STR(node.bablock.type)
    if node.is_after_block():
        return 'AFTER ' + STR(node.bablock.type)
    if name := getattr(node, 'name', None):
        return STR(name)
    try:
        return STR(node.get_nmodl_name())
    except RuntimeError:
        return STR(node.get_node_type_name())

def prepend_line_numbers(string):
    lines = string.split('\n')
    lines = (str(num+1).rjust(2) + ": " + text for num, text in enumerate(lines))
    return '\n'.join(lines)

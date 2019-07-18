#!/usr/bin/python3
'''Run embedded Python scripts in HDL code. Make LUTs look tidier.

All the contents between pairs of "```"s and "`"s are considered Python code (similar as markdown). They are run consecutively in the SAME context. Their place in the generated HDL code are replaced with what the Python snippets send to stdout.

For example, the HDL code may make use of a lookup table:

    function [1:0] row;
    input wire[3:0] addr;
    case(addr)
    0,1,2,3: row=0
    4,5,6,7: row=1
    8,9,10,11: row=2
    12,13,14,15: row=3
    endcase
    endfunction

However, we might need to change the table in the future, or the table might be too big so make the code unreadable. So we use python to generate the table:
    `row_len=2`
    `addr_len=row_len ** 2`
    function [`row_len-1`:0] row;
    input wire[`addr_len-1`:0] addr;
    case(addr)
    ```
    for i in range(addr_len):
        print(str(list(range(i*addr_len, i*addr_len + addr_len, 1)))[1:-1] + ": row="+str(i))
    ```
    endcase
    endfunction

After running the pyinhdl script, it generates exactly the same Verilog code as above.

Notes:
    1. The Python are executed in the SAME CONTEXT throughout for a file. So if you define a variable, then it's visible for all the snippets below it. Del it at the end if you don't want to use it later. 
    2. For inline snippet, if it's a expression, then the __str__ method of the expression is called. so that you needn't call print for simple substitution. Note that the __str__ method of list contain brackets! For block snippet, only the output to stdout are considered.
    3. For inline snippets, the output or result of the python code replaces the original source starting at the first "`". For block snippets, all the output are padded with spaces of the same length as the space before the "```". Though I haven't yet heard of a hdl language that place syntaxtic value on whitespace, this design choice would make it easier for you to read the generated hdl.  
    4. If the Python code is too complex, you may import it for Python files placed in the same directory as the HDL code. You can configure it with the commandline option --import-dir. The standard libraries and all the third party libraries can be used normally.
    5. Comments aren't ignored: if there were "```"s or "`"s in comments, then the code would be executed all the same.    6. Though the snippets are executed in a different namespace, they are in no way sandboxed. So don't run untrusted code, and don't use exotic system functions. 
'''
import re
import sys
import argparse
import multiprocessing
import io

# Global dict for sharing the context. I asked it! https://stackoverflow.com/questions/57091608/in-python-how-to-use-many-exec-calls-in-a-shared-context
global_dict = dict()
def exec_inline(src):
    global global_dict
    is_expression = True
    try:
        code = compile(src, '<stdin>', 'eval')
    except SyntaxError:
        is_expression= False
        code = compile(src, '<stdin>', 'exec')
    if is_expression:
        # TODO: Show the code's errors more clearly.
        res = eval(code, global_dict)
        return str(res)
    else:
        old_stdout = sys.stdout
        s = io.StringIO()
        sys.stdout = s
        exec(code, global_dict)
        res = s.getvalue()
        s.close()
        sys.stdout = old_stdout
        return res

def exec_block(src):
    global global_dict
    # see https://stackoverflow.com/questions/3906232/python-get-the-print-output-in-an-exec-statement for io redirection
    old_stdout = sys.stdout
    s = io.StringIO()
    sys.stdout = s
    exec(src, global_dict)
    res = s.getvalue()
    s.close()
    sys.stdout = old_stdout

    return res

def pre_context_run(import_pathes=None):
    '''Sets up the context (global namespace) for the snippets to exec in.
    '''
    global global_dict
    exec('import sys\n', global_dict)
    if import_pathes:
        for import_path in import_pathes:
            # TODO: injection prevention?
            exec('sys.path.insert(0, "%s")\n' % (import_path), global_dict)


def parse(fp, fp_out, import_pathes=None):
    '''Parse the hdl file, and outputs the file with Python executed.
    
    Args:
        fp : file object for the input hdl file.
        fp_out : destination for the output file.
    
    Returns:
        None
    
    Raises:
        SyntaxError if the code blocks are not closed.
    '''
    def add_space(s, _indentation):
        ans = ""
        for i in s.split("\n"):
            if i.strip() == "":
                continue
            ans += "".rjust(_indentation) + i + "\n"
        return ans
    state = "hdl"
    py_block_snippet = ""
    indentation = 0
    pre_context_run(import_pathes)
    for l in fp:
        if state == "hdl":
            if l.strip().startswith("```"):
                state = "block"
                indentation = len(l) - len(l.lstrip())
            elif "`" in l:
                # TODO: someway to escape "`'? Though I don't recall any legitimate use of "`"...
                ans = ""
                pos = 0
                codes_iter = re.finditer("`[^`]*`", l)
                for i in codes_iter:
                    res = exec_inline(i.group()[1:-1]) # don't forget the ` and the ` at the end!
                    start, end = i.span()
                    if not res:
                        res = ""
                    ans += (l[pos:start] + res)
                    pos = end
                ans += l[pos:]
                if ans.strip() != "":
                    fp_out.write(ans)
            else:
                fp_out.write(l)
        elif state == "block":
            if l.strip().startswith("```"):
                res = exec_block(py_block_snippet)
                if not res:
                    res = ""
                fp_out.write(add_space(res, indentation))
                py_block_snippet = ""
                indentation = 0
                state = "hdl"
            else:
                py_block_snippet += l[indentation:]
    global global_dict
    global_dict = {}
    if state == "block":
        raise SyntaxError("There exists block Python code that is not closed!")
    return


if __name__ == "__main__":
    import pathlib
    parser = argparse.ArgumentParser()
    parser.add_argument(metavar='input_path', dest="input", type=pathlib.Path,  help='Path of the file to process.')
    parser.add_argument(metavar='output_directory', dest="output_dir", type=pathlib.Path,  help='Path of the directory to output the result.')  
    parser.add_argument('-r', '--recursive',  action='store_true', help='Go into the folder recursively.')
    parser.add_argument('--import-dir', dest="import_dir", type=pathlib.Path, help='The directory to search for other Python scripts. Default to the directory where the hdl codes are.')
    # TODO: implement other output channels and other formats.
    args = parser.parse_args()
    allowed_suffixes = ["v"]
    if args.input.exists():
        try:
            args.output_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            print("There's a file with the same name as the output directory! You might want to delete that file.", file=sys.stderr)
            sys.exit(1)
        import_pathes = []
        if args.input.is_dir():
            import_pathes.append(str(args.input))
        if args.import_dir: 
            if args.import_dir.exists() and args.import_dir.is_dir():
                import_pathes.append(str(args.import_dir))
            else:
                print("The import directory specified does not exist.", file=sys.stderr)
                sys.exit(1)
        if args.input.is_file():
            with args.input.open() as fp:
                with (args.output_dir / args.input.name).open("w") as fp_out:
                    parse(fp, fp_out, import_pathes)
        elif args.input.is_dir():
            if not args.recursive:
                print("Input path is a folder. Use -r to recursively look into the folder.", file=sys.stderr)
                sys.exit(1)
            else:
                for suffix in allowed_suffixes:
                    for p in args.input.glob("**/*." + suffix):
                        par = args.output_dir / p.parent.relative_to(args.input)
                        par.mkdir(parents=True, exist_ok=True)
                        with p.open() as fp: 
                            with (args.output_dir / p.relative_to(args.input)).open("w") as fp_out:
                                parse(fp, fp_out, import_pathes)
            
    else:
        print("Input path doesn't exist!", file=sys.stderr)
        sys.exit(1)

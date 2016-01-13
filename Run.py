import os
import sys
import uncompyle2

def decompile(file):
    
    if (os.path.isdir(file)):
        dir = file
        files = os.listdir(dir)
        for f in files:
            f = dir + "\\" + f
            if (os.path.isdir(f)):
                decompile(f)
            else:
                base, ext = os.path.splitext(f)
                if ext == ".pyc" or ext == ".pyo":
                    print("file: " + f)
                    newfile = base +".uncompyled.py"
                    print("new file: " + newfile)
                    with open(newfile, "wb") as fileobj:
                        uncompyle2.uncompyle_file(f, fileobj)
    elif (os.path.isfile(file)):
        print("file: " + file)
        base, ext = os.path.splitext(file)
        if ext == ".pyc" or ext == ".pyo":
            newfile = base + ".uncompyled.py"
            print("new file: " + newfile)
            fileToDecompile = file
            with open(newfile, "wb") as fileobj:
                uncompyle2.uncompyle_file(fileToDecompile, fileobj)   
    else:
        print("Error: file not found!")
        raw_input()
        exit(1)

if __name__ == "__main__":
    if (len(sys.argv) < 2):
        print("Error: missing file arg!")
        raw_input()
        exit(1)
    
    filename = sys.argv[1]
    
    print("Decompiling....")    
    
    decompile(filename) 
    
    print("Done!")
    raw_input("Press any key to continue...")    

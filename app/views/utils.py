
import threading
import os
import time
import json
import math
import cv2
import psutil
import hashlib
import yaml
from functools import wraps

try:
    import GPUtil
except ImportError:
    GPUtil = None

# from constant import *

def load_label(path):
    lines = []
    try:
        with open(path,"r") as ff:
            lines = ff.readlines()
            ff.close()
        return [l.strip().strip("\n") for l in lines]
    except:
        pass
    return lines

def sorting_pair(l1,l2,key,reverse=False):
    a = list(zip(*sorted(zip(l1, l2),key=key,reverse=reverse)))
    if len(a):
        return list(a[0]),list(a[1])
    else:
        return l1,l2

def format_ex(ex):
    return f"{type(ex)}: {ex}"

def generateColorByText(text):
    s = text
    hashCode = int(hashlib.sha256(s.encode('utf-8')).hexdigest(), 16)
    r = int((hashCode / 255) % 255)
    g = int((hashCode / 65025)  % 255)
    b = int((hashCode / 16581375)  % 255)
    return (r, g, b)

def save_json(filename,data):
    with open(filename,"w") as ff:
        data_store = json.dumps(data,sort_keys=True,indent=4)
        ff.write(data_store)
        ff.close()

def load_json(filename):
    data = {}
    with open(filename) as ff:
        data = json.load(ff)
        ff.close()
    return data

def get_cpu_ram_usage():
    d = dict(psutil.virtual_memory()._asdict())
    cpu = psutil.cpu_percent()

    total = d["total"]
    used = d["used"]

    phycial_memory = used/total*100

    return (cpu, phycial_memory)

def get_list_gpus():
    if GPUtil is None:
        return []
    try:
        gpus = GPUtil.getGPUs()
        return gpus
    except:
        return []

def get_hardware_resoures():
    resources = {"cpu":{}, "gpu":{}, "ram": {}}

    resources["cpu"]["percent"] = psutil.cpu_percent(interval=1)
    resources["ram"]["percent"] = psutil.virtual_memory().percent

    gpus = get_list_gpus()
    resources["gpus"] = {}
    for i, gpu in enumerate(gpus):
        resources["gpus"][i] = {}
        resources["gpus"][i]["name"] = gpu.name
        resources["gpus"][i]["percent"] = gpu.load
        resources["gpus"][i]["temperature"] = gpu.temperature
    
    return resources

def load_yaml(path="cameras.yaml"):
    config = None
    with open(path, "r") as file:
        config = yaml.safe_load(file)
        file.close()
    return config

def save_yaml(path, data):
    with open(path, "w") as file:
        yaml.safe_dump(data, file)
        file.close()

def str2int(s, default=0):
    try:
        return int(s)
    except:
        return default

def str2float(s, default=0.):
    try:
        return float(s)
    except:
        return default

def str2ListInt(string, sep=","):
    lst = string.split(sep)
    return [int(l) for l in lst]

def str2ListFloat(string, sep=","):
    lst = string.split(sep)
    return [float(l) for l in lst]

def mkdir(folder):
    os.makedirs(folder, exist_ok=True)
    return folder

def runThread(target, args=(), daemon=False):
    thread = threading.Thread(target=target, args=args, daemon=daemon)
    thread.start()
    
def rgb_to_hex(rgb):
    return '#%02x%02x%02x' % rgb

def bin2dec(b):
    p = 0
    dec = 0
    r = 0
    n = -1
    while b >= 2:
        r = b % 10
        b = (b - r) // 10
        n += 1
        dec += math.pow(2, n)*r
    dec += math.pow(2, n+1)*b
    return int(dec)

def decorator_dt(f):   
    t0 = time.time()  
    @wraps(f)
    def wrapper(*args, **kwds):
        try:         
            return f(*args, **kwds)
        finally:
            print("time do %s : "%f.__name__,time.time() - t0)
    return wrapper

def t_img(mat):
    if len(mat.shape) == 2:
        return mat.T
    else:
        b,g,r = cv2.split(mat)
        bT = b.T
        gT = g.T
        rT = r.T
        return cv2.merge((bT,gT,rT))

def cv_rotated(mat,deg):
    if deg == 90:
        return cv2.flip(t_img(mat),1)
    elif deg == 180:
        return cv2.flip(mat,-1)
    elif deg == 270:
        return cv2.flip(t_img(mat),0)
    pass

def scan_dir(folder):
    '''
    return size(Mb)
    '''
    size = 0
    n = 0
    n_dir = 0
    for path,dirs,files in os.walk(folder):
        # print(path)
        n_dir += len(dirs)
        for f in files:
            fp = os.path.join(path,f)
            n += 1
            try:
                size += os.path.getsize(fp)
            except Exception as ex:
                pass
    
    size = size/1024**2
    return size

if __name__ == "__main__":
    pass


    
    

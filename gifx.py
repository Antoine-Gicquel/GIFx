#!/usr/bin/python3

import os
import atexit
import subprocess
import time
import shlex
import logging
import signal
from datetime import datetime

fifo_path = "./gifpipe"
screen_size = "1920x1080"
fps = 2
duration = 15

gifLength = fps*duration
mode = 0o600
ffmpeg_command = f"ffmpeg -video_size {screen_size} -framerate {fps} -f x11grab -i :0.0 -lavfi palettegen=stats_mode=single[pal],[0:v][pal]paletteuse=new=1 -f gif pipe:1 > {fifo_path}"


loggerz = logging.getLogger(__name__)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
loggerz.addHandler(ch)
loggerz.setLevel(logging.INFO)


class FIFOGif(object):
    def __init__(self, size=2*30):
        self.gifLength = size
        self._blkTypes = [None for _ in range(self.gifLength * 2)] # faire tourner la liste
        self._extBlocks = [None for _ in range(self.gifLength * 2)] # faire tourner la liste
        self._imageDescriptors = [None for _ in range(self.gifLength * 2)] # faire tourner la liste
        self._blkPointer = 0
        self._extPointer = 0
        self._imagePointer = 0
        self._toSave = False
        return

    def parseHeader(self, fd):
        self.signature = fd.read(len(b"GIF89a"))
        loggerz.debug(f"Parsing File Format signature : {self.signature.decode()}")

    def encodeHeader(self):
        return self.signature

    def parseLogicalScreen(self, fd):
        loggerz.debug("Parsing Logical Screen")
        self._width = int.from_bytes(fd.read(2), byteorder='little')
        self._height = int.from_bytes(fd.read(2), byteorder='little')
        loggerz.debug(f"\t{self._width}x{self._height}")
        self._gctInfo = int.from_bytes(fd.read(1), byteorder='little')
        self._gctPresent = bool(self._gctInfo & 0b10000000)
        loggerz.debug(f"\tGCT is {'not '*(1-self._gctPresent)}present")
        self._gctBitRes = ((self._gctInfo >> 4) & 0b00000111) + 1
        loggerz.debug(f"\tGCT bit resolution : {self._gctBitRes}")
        self._gctSize = 2**((self._gctInfo & 0b00000111) + 1)
        loggerz.debug(f"\tGCT size : {self._gctSize}")
        self._globalBackgroundIndex = int.from_bytes(fd.read(1), byteorder='little')
        loggerz.debug(f"\tGlobal background index : {self._globalBackgroundIndex}")
        self._pixelAspectRatio = int.from_bytes(fd.read(1), byteorder='little')
        loggerz.debug(f"\Pixel aspect ratio : {self._pixelAspectRatio}")

    def encodeLogicalScreen(self):
        r = b""
        r += int.to_bytes(self._width, length=2, byteorder="little")
        r += int.to_bytes(self._height, length=2, byteorder="little")
        r += int.to_bytes(self._gctInfo, length=1, byteorder="little")
        r += int.to_bytes(self._globalBackgroundIndex, length=1, byteorder="little")
        r += int.to_bytes(self._pixelAspectRatio, length=1, byteorder="little")
        return r

    def parseGCT(self, fd):
        loggerz.debug("Parsing GCT")
        self._gct = []
        if self._gctPresent:
            assert self._gctBitRes == 8 # Sinon c'est la merde, ça fait pas 3 octets par couleur
            for i in range(self._gctSize):
                x = fd.read(3) # 3 octets = 24 bits = 3*self._gctBitRes
                loggerz.debug(f"GCT color {len(self._gct)} : { ' '.join([str(int(u)) for u in x]) }")
                self._gct.append(x)

    def encodeGCT(self):
        r = b""
        for i in range(self._gctSize):
            r += self._gct[i]
        return r

    def parseExtBlock(self, fd):
        loggerz.debug("Parsing extension block")
        self._extBlocks[self._extPointer] = dict()
        self._extBlocks[self._extPointer]["blkType"] = fd.read(1)
        loggerz.debug(f'\tType : { self._extBlocks[self._imagePointer]["blkType"] }')
        self._extBlocks[self._extPointer]["blkData"] = []
        blkLen = 1
        while blkLen != 0:
            blkLen = int.from_bytes(fd.read(1), byteorder='little')
            self._extBlocks[self._extPointer]["blkData"].append(fd.read(blkLen))
        loggerz.debug(f'\tData : { self._extBlocks[self._imagePointer]["blkData"][:4] } + ... + { self._extBlocks[self._imagePointer]["blkData"][-1] }')
        self._extPointer = (self._extPointer + 1) % len(self._extBlocks)

    def encodeExtension(self, ext):
        r = b""
        r += ext["blkType"]
        for b in ext["blkData"]:
            r += int.to_bytes(len(b), length=1, byteorder="little")
            r += b
        return r

    def parseImageDescriptor(self, fd):
        loggerz.debug("Parsing image descriptor")
        self._imageDescriptors[self._imagePointer] = dict()
        self._imageDescriptors[self._imagePointer]["top_left"] = fd.read(4)
        loggerz.debug(f'\tTop-left corner : {str(self._imageDescriptors[self._imagePointer]["top_left"])}')
        self._imageDescriptors[self._imagePointer]["bottom_right"] = fd.read(4)
        loggerz.debug(f'\tBottom-right corner : {str(self._imageDescriptors[self._imagePointer]["bottom_right"])}')
        self._imageDescriptors[self._imagePointer]["localMapInfo"] = int.from_bytes(fd.read(1), byteorder='little')
        self._imageDescriptors[self._imagePointer]["useLocalMap"] = bool(self._imageDescriptors[self._imagePointer]["localMapInfo"] & 0b10000000)
        loggerz.debug(f'\tUse local map : {str(self._imageDescriptors[self._imagePointer]["useLocalMap"])}')
        self._imageDescriptors[self._imagePointer]["interlaced"] = bool(self._imageDescriptors[self._imagePointer]["localMapInfo"] & 0b01000000)
        loggerz.debug(f'\tInterlaced : {str(self._imageDescriptors[self._imagePointer]["interlaced"])}')
        self._imageDescriptors[self._imagePointer]["localMapLength"] = 2**((self._imageDescriptors[self._imagePointer]["localMapInfo"] & 0b0000111) + 1)
        loggerz.debug(f'\tLocal map length : {str(self._imageDescriptors[self._imagePointer]["localMapLength"])}')
        if self._imageDescriptors[self._imagePointer]["useLocalMap"]:
            loggerz.debug("\tParsing local map")
            self._imageDescriptors[self._imagePointer]["localColorMap"] = []
            for i in range(self._imageDescriptors[self._imagePointer]["localMapLength"]):
                x = fd.read(3)
                loggerz.debug(f'\t\tLocal color {len(self._imageDescriptors[self._imagePointer]["localColorMap"])} : { str(x) }')
                self._imageDescriptors[self._imagePointer]["localColorMap"].append(x)
        self._imageDescriptors[self._imagePointer]["imageDataStart"] = fd.read(1)
        loggerz.debug(f'\tImage Data Start : { self._imageDescriptors[self._imagePointer]["imageDataStart"] }')
        self._imageDescriptors[self._imagePointer]["imageData"] = []
        subBlkLen = int.from_bytes(fd.read(1), byteorder='little')
        loggerz.debug(f'\t\tData subBlock length : { subBlkLen }')
        while subBlkLen > 0:
            self._imageDescriptors[self._imagePointer]["imageData"].append(fd.read(subBlkLen))
            subBlkLen = int.from_bytes(fd.read(1), byteorder='little')
            loggerz.debug(f'\t\tData subBlock length : { subBlkLen }')
        self._imageDescriptors[self._imagePointer]["imageData"].append(b'')
        self._imagePointer = (self._imagePointer + 1) % len(self._imageDescriptors)

    def encodeImage(self, image):
        r = b""
        r += image["top_left"]
        r += image["bottom_right"]
        r += int.to_bytes(image["localMapInfo"], length=1, byteorder="little")
        if image["useLocalMap"]:
            for i in range(image["localMapLength"]):
                r += image["localColorMap"][i]
        r += image["imageDataStart"]
        for d in image["imageData"]:
            r += int.to_bytes(len(d), length=1, byteorder="little")
            r += d
        return r

    def parseBlock(self, fd):
        x = fd.read(1)
        self._blkTypes[self._blkPointer] = x
        loggerz.info(f"New block of type : {str(self._blkTypes[self._blkPointer])}")
        self._blkPointer = (self._blkPointer + 1) % len(self._blkTypes)
        if x == b",":
            self.parseImageDescriptor(fd)
        elif x == b"!":
            self.parseExtBlock(fd)
        elif x == b";":
            pass
        return x

    def parseAllHead(self, fd):
        self.parseHeader(fd)
        self.parseLogicalScreen(fd)
        self.parseGCT(fd)

    def encode(self):
        nbImg = 0
        nbExt = 0
        blkCounter = 0
        blks = []
        L = []
        while nbImg < self.gifLength and self._blkTypes[(self._blkPointer - blkCounter - 1) % len(self._blkTypes)] != None:
            blkType = self._blkTypes[(self._blkPointer - blkCounter - 1) % len(self._blkTypes)]
            L.append(blkType)
            blkCounter += 1
            if blkType == b",":
                nbImg += 1
                blks.append(self._imageDescriptors[(self._imagePointer - nbImg) % len(self._imageDescriptors)])
            elif blkType == b"!":
                nbExt += 1
                blks.append(self._extBlocks[(self._extPointer - nbExt) % len(self._extBlocks)])
        blks = blks[::-1]
        L = L[::-1]
        print(len(blks))
        print(self.gifLength)

        # Building the Gif
        fullGif = b""
        fullGif += self.encodeHeader()
        fullGif += self.encodeLogicalScreen()
        fullGif += self.encodeGCT()
        for i in range(len(L)):
            fullGif += L[i]
            if L[i] == b",":
                fullGif += self.encodeImage(blks[i])
            elif L[i] == b"!":
                fullGif += self.encodeExtension(blks[i])
        fullGif += b";"
        return fullGif

    def save(self, path):
        data = self.encode()
        with open(os.path.join(path, datetime.now().strftime("%d%m%Y_%H%M%S") + ".gif"), "wb+") as f:
            f.write(data)
        self._toSave = False

    def toSave(self, signum, stack):
        self._toSave = True


@atexit.register
def cleanup():
    try:
        os.unlink(fifo_path)
    except:
        pass

def init():
    os.mkfifo(fifo_path, mode)
    p = subprocess.Popen(ffmpeg_command, shell=True)
    return p

if __name__ == "__main__":
    save_path = "./"
    G = FIFOGif()
    init()
    time.sleep(10)
    f = open(fifo_path, "rb", buffering=512)
    G.parseAllHead(f)
    signal.signal(signal.SIGUSR1, G.toSave)
    while True:
        x = G.parseBlock(f)
        if x == b"," and G._toSave:
            G.save(save_path)
    cleanup()

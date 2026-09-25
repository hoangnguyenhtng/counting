# -- coding: utf-8 --
import sys
import threading
import msvcrt
import tkinter.messagebox
import numpy as np
import cv2
import time
import sys, os
import datetime
import inspect
import ctypes
import random
from ctypes import *
from hardware.cameras.MVSImport.MvCameraControl_class import *
import time

def getStrDateTime():
    return time.strftime("%d%m%y_%H%M%S")
# 

def message_warning(title,stt):
    # QMessageBox.warning(None,title,stt)
    tkinter.messagebox.showwarning(title, stt)
    pass

def message_info(title,stt):
    # QMessageBox.information(None,title,stt)
    tkinter.messagebox.showinfo(title, stt)
    pass

def Async_raise(tid, exctype):
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        raise SystemError("PyThreadState_SetAsyncExc failed")

def Stop_thread(thread):
    Async_raise(thread.ident, SystemExit)

class CameraOperation():
    # data_signal = pyqtSignal(np.ndarray)

    def __init__(self,obj_cam,st_device_list,n_connect_num=0,b_open_device=False,b_start_grabbing = False,h_thread_handle=None,\
                b_thread_closed=False,st_frame_info=None,buf_cache=None,b_exit=False,b_save_bmp=False,b_save_jpg=False,buf_save_image=None,\
                n_save_image_size=0,n_payload_size=0,n_win_gui_id=0,frame_rate=0,exposure_time=0,gain=0,
                named_window=None,draw_boxes=[]):
        # byme
        super(CameraOperation,self).__init__()
        # 
        self.obj_cam = obj_cam
        self.st_device_list = st_device_list
        self.n_connect_num = n_connect_num
        self.b_open_device = b_open_device
        self.b_start_grabbing = b_start_grabbing 
        self.b_thread_closed = b_thread_closed
        self.st_frame_info = st_frame_info
        self.buf_cache = buf_cache
        self.b_exit = b_exit
        self.b_save_bmp = b_save_bmp
        self.b_save_jpg = b_save_jpg
        self.n_payload_size = n_payload_size
        self.buf_save_image = buf_save_image
        self.h_thread_handle = h_thread_handle
        self.n_win_gui_id = n_win_gui_id
        self.n_save_image_size = n_save_image_size
        self.b_thread_closed
        self.frame_rate = frame_rate
        self.exposure_time = exposure_time
        self.gain = gain
        self.bRun = False
        self.named_window = named_window
        self.mat = None
        self.boxes = draw_boxes

    def set_draw_boxes(self,boxes):
        self.boxes = boxes
        pass

    def To_hex_str(self,num):
        chaDic = {10: 'a', 11: 'b', 12: 'c', 13: 'd', 14: 'e', 15: 'f'}
        hexStr = ""
        if num < 0:
            num = num + 2**32
        while num >= 16:
            digit = num % 16
            hexStr = chaDic.get(digit, str(digit)) + hexStr
            num //= 16
        hexStr = chaDic.get(num, str(num)) + hexStr   
        return hexStr

    def Open_device(self):
        if False == self.b_open_device:
            # ch:选择设备并创建句柄 | en:Select device and create handle
            nConnectionNum = int(self.n_connect_num)
            stDeviceList = cast(self.st_device_list.pDeviceInfo[int(nConnectionNum)], POINTER(MV_CC_DEVICE_INFO)).contents
            self.obj_cam = MvCamera()
            ret = self.obj_cam.MV_CC_CreateHandle(stDeviceList)
            if ret != 0:
                self.obj_cam.MV_CC_DestroyHandle()
                # message_warning('show error','create handle fail! ret = '+ self.To_hex_str(ret))
                return ret

            ret = self.obj_cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
            if ret != 0:
                # message_warning('show error','open device fail! ret = '+ self.To_hex_str(ret))
                return ret
            # print ("open device successfully!")
            self.b_open_device = True
            self.b_thread_closed = False

            # ch:探测网络最佳包大小(只对GigE相机有效) | en:Detection network optimal package size(It only works for the GigE camera)
            if stDeviceList.nTLayerType == MV_GIGE_DEVICE:
                nPacketSize = self.obj_cam.MV_CC_GetOptimalPacketSize()
                if int(nPacketSize) > 0:
                    ret = self.obj_cam.MV_CC_SetIntValue("GevSCPSPacketSize",nPacketSize)
                    if ret != 0:
                        print ("warning: set packet size fail! ret[0x%x]" % ret)
                else:
                    print ("warning: set packet size fail! ret[0x%x]" % nPacketSize)

            stBool = c_bool(False)
            ret =self.obj_cam.MV_CC_GetBoolValue("AcquisitionFrameRateEnable", byref(stBool))
            if ret != 0:
                print ("get acquisition frame rate enable fail! ret[0x%x]" % ret)

            stParam =  MVCC_INTVALUE()
            memset(byref(stParam), 0, sizeof(MVCC_INTVALUE))
            
            ret = self.obj_cam.MV_CC_GetIntValue("PayloadSize", stParam)
            if ret != 0:
                print ("get payload size fail! ret[0x%x]" % ret)
            self.n_payload_size = stParam.nCurValue
            if None == self.buf_cache:
                self.buf_cache = (c_ubyte * self.n_payload_size)()

            # ch:设置触发模式为off | en:Set trigger mode as off
            ret = self.obj_cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
            if ret != 0:
                print ("set trigger mode fail! ret[0x%x]" % ret)
            return 0

    def Start_grabbing(self):
        if False == self.b_start_grabbing and True == self.b_open_device:
            self.b_exit = False
            ret = self.obj_cam.MV_CC_StartGrabbing()
            if ret != 0:
                # message_warning('show error','start grabbing fail! ret = '+ self.To_hex_str(ret))
                return
            self.b_start_grabbing = True
            # print ("start grabbing successfully!")
            try:
                self.n_win_gui_id = random.randint(1,10000)
                # self.bRun = True
                self.h_thread_handle = threading.Thread(target=CameraOperation.Work_thread, args=(self,))
                self.h_thread_handle.start()
                self.b_thread_closed = True
            except:
                # message_warning('show error','error: unable to start thread')
                False == self.b_start_grabbing

    def Stop_grabbing(self):
        if True == self.b_start_grabbing and self.b_open_device == True:
            #退出线程
            # if True == self.b_thread_closed:
            #     Stop_thread(self.h_thread_handle)
            #     # self.bRun = False
            #     self.b_thread_closed = False
            ret = self.obj_cam.MV_CC_StopGrabbing()
            if ret != 0:
                # message_warning('show error','stop grabbing fail! ret = '+self.To_hex_str(ret))
                return False
            # print ("stop grabbing successfully!")
            self.b_start_grabbing = False
            self.b_exit  = True
            return True      

    def Close_device(self):
        if True == self.b_open_device:
            #退出线程
            # if True == self.b_thread_closed:
            #     Stop_thread(self.h_thread_handle)
            #     self.b_thread_closed = False
            ret = self.obj_cam.MV_CC_CloseDevice()
            if ret != 0:
                # message_warning('show error','close deivce fail! ret = '+self.To_hex_str(ret))
                return False
                
        # ch:销毁句柄 | Destroy handle
        self.obj_cam.MV_CC_DestroyHandle()
        self.b_open_device = False
        self.b_start_grabbing = False
        self.b_exit  = True
        return True
        # print ("close device successfully!")

    def Set_trigger_mode(self,strMode):
        if True == self.b_open_device:
            if "continuous" == strMode: 
                ret = self.obj_cam.MV_CC_SetEnumValue("TriggerMode",0)
                if ret != 0:
                    message_warning('show error','set triggermode fail! ret = '+self.To_hex_str(ret))
            if "triggermode" == strMode:
                ret = self.obj_cam.MV_CC_SetEnumValue("TriggerMode",1)
                if ret != 0:
                    message_warning('show error','set triggermode fail! ret = '+self.To_hex_str(ret))
                ret = self.obj_cam.MV_CC_SetEnumValue("TriggerSource",7)
                if ret != 0:
                    message_warning('show error','set triggersource fail! ret = '+self.To_hex_str(ret))

    def Trigger_once(self,nCommand):
        if True == self.b_open_device:
            if 1 == nCommand: 
                ret = self.obj_cam.MV_CC_SetCommandValue("TriggerSoftware")
                if ret != 0:
                    message_warning('show error','set triggersoftware fail! ret = '+self.To_hex_str(ret))

    def Get_parameter(self):
        if True == self.b_open_device:
            stFloatParam_FrameRate =  MVCC_FLOATVALUE()
            memset(byref(stFloatParam_FrameRate), 0, sizeof(MVCC_FLOATVALUE))
            stFloatParam_exposureTime = MVCC_FLOATVALUE()
            memset(byref(stFloatParam_exposureTime), 0, sizeof(MVCC_FLOATVALUE))
            stFloatParam_gain = MVCC_FLOATVALUE()
            memset(byref(stFloatParam_gain), 0, sizeof(MVCC_FLOATVALUE))
            ret = self.obj_cam.MV_CC_GetFloatValue("AcquisitionFrameRate", stFloatParam_FrameRate)
            if ret != 0:
                message_warning('show error','get acquistion frame rate fail! ret = '+self.To_hex_str(ret))
            self.frame_rate = stFloatParam_FrameRate.fCurValue
            ret = self.obj_cam.MV_CC_GetFloatValue("ExposureTime", stFloatParam_exposureTime)
            if ret != 0:
                message_warning('show error','get exposure time fail! ret = '+self.To_hex_str(ret))
            self.exposure_time = stFloatParam_exposureTime.fCurValue
            ret = self.obj_cam.MV_CC_GetFloatValue("Gain", stFloatParam_gain)
            if ret != 0:
                message_warning('show error','get gain fail! ret = '+self.To_hex_str(ret))
            self.gain = stFloatParam_gain.fCurValue
            # tkinter.messagebox.showinfo('show info','get parameter success!')

    def Set_parameter(self,frameRate,exposureTime,gain):
        if '' == frameRate or '' == exposureTime or '' == gain:
            # tkinter.messagebox.showinfo('show info','please type in the text box !')
            return
        if True == self.b_open_device:
            ret = self.obj_cam.MV_CC_SetFloatValue("ExposureTime",float(exposureTime))
            if ret != 0:
                message_warning('show error','set exposure time fail! ret = '+self.To_hex_str(ret))

            ret = self.obj_cam.MV_CC_SetFloatValue("Gain",float(gain))
            if ret != 0:
                message_warning('show error','set gain fail! ret = '+self.To_hex_str(ret))

            ret = self.obj_cam.MV_CC_SetFloatValue("AcquisitionFrameRate",float(frameRate))
            if ret != 0:
                message_warning('show error','set acquistion frame rate fail! ret = '+self.To_hex_str(ret))

            # tkinter.messagebox.showinfo('show info','set parameter success!')

    def Work_thread(self):
        # ch:创建显示的窗口 | en:Create the window for display
        if  isinstance(self.named_window,str):
            cv2.namedWindow(self.named_window,cv2.WINDOW_FREERATIO)
            cv2.resizeWindow(self.named_window, 640, 480)
        # cv2.resizeWindow(str(self.n_win_gui_id), 500, 500)
        stFrameInfo = MV_FRAME_OUT_INFO_EX()  
        # 
        while self.bRun:
            t0 = time.time()
            ret = self.obj_cam.MV_CC_GetOneFrameTimeout(byref(self.buf_cache), self.n_payload_size, stFrameInfo, 1000)
            if ret == 0:
                img_buff = None
                #获取到图像的时间开始节点获取到图像的时间开始节点
                self.st_frame_info = stFrameInfo
                # print ("get one frame: Width[%d], Height[%d], nFrameNum[%d]"  % (self.st_frame_info.nWidth, self.st_frame_info.nHeight, self.st_frame_info.nFrameNum))
                #######################################
                self.n_save_image_size = self.st_frame_info.nWidth * self.st_frame_info.nHeight * 3 + 2048
                if img_buff is None:
                   img_buff = (c_ubyte * self.n_save_image_size)()
                #######################################
                # if True == self.b_save_jpg:
                #     self.Save_jpg() #ch:保存Jpg图片 | en:Save Jpg
                # if self.buf_save_image is None:
                #     self.buf_save_image = (c_ubyte * self.n_save_image_size)()

                # stParam = MV_SAVE_IMAGE_PARAM_EX()
                # stParam.enImageType = MV_Image_Bmp;                                        # ch:需要保存的图像类型 | en:Image format to save
                # stParam.enPixelType = self.st_frame_info.enPixelType                               # ch:相机对应的像素格式 | en:Camera pixel type
                # stParam.nWidth      = self.st_frame_info.nWidth                                    # ch:相机对应的宽 | en:Width
                # stParam.nHeight     = self.st_frame_info.nHeight                                   # ch:相机对应的高 | en:Height
                # stParam.nDataLen    = self.st_frame_info.nFrameLen
                # stParam.pData       = cast(self.buf_cache, POINTER(c_ubyte))
                # stParam.pImageBuffer =  cast(byref(self.buf_save_image), POINTER(c_ubyte)) 
                # stParam.nBufferSize = self.n_save_image_size                                 # ch:存储节点的大小 | en:Buffer node size
                # stParam.nJpgQuality     = 80;                                                # ch:jpg编码，仅在保存Jpg图像时有效。保存BMP时SDK内忽略该参数
                # if True == self.b_save_bmp:
                #     self.Save_Bmp() #ch:保存Bmp图片 | en:Save Bmp
            else:
                continue

            ## 转换像素结构体赋值
            # stConvertParam = MV_CC_PIXEL_CONVERT_PARAM()
            # memset(byref(stConvertParam), 0, sizeof(stConvertParam))
            # stConvertParam.nWidth = self.st_frame_info.nWidth
            # stConvertParam.nHeight = self.st_frame_info.nHeight
            # stConvertParam.pSrcData = self.buf_cache
            # stConvertParam.nSrcDataLen = self.st_frame_info.nFrameLen
            # stConvertParam.enSrcPixelType = self.st_frame_info.enPixelType 

            # Mono8直接显示
            if PixelType_Gvsp_Mono8 == self.st_frame_info.enPixelType:
                numArray = CameraOperation.Mono_numpy(self,self.buf_cache,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
            
            elif PixelType_Gvsp_BayerGB8 == self.st_frame_info.enPixelType:
                numArray = CameraOperation.Mono_numpy(self,self.buf_cache,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
                self.mat = cv2.cvtColor(numArray,cv2.COLOR_BAYER_GB2RGB)
                # self.data_signal.emit(self.mat)
            
            elif PixelType_Gvsp_BayerRG8 == self.st_frame_info.enPixelType:
                numArray = CameraOperation.Mono_numpy(self,self.buf_cache,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
                self.mat = cv2.cvtColor(numArray,cv2.COLOR_BAYER_RG2RGB)
                # self.data_signal.emit(self.mat)
                # dt = time.time() - t0
                # print("PixelType_Gvsp_BayerGB8 : ",dt)

            # RGB直接显示
            elif PixelType_Gvsp_RGB8_Packed == self.st_frame_info.enPixelType:
                numArray = CameraOperation.Color_numpy(self,self.buf_cache,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
                

            #如果是黑白且非Mono8则转为Mono8
            elif  True == self.Is_mono_data(self.st_frame_info.enPixelType):
                nConvertSize = self.st_frame_info.nWidth * self.st_frame_info.nHeight
                stConvertParam.enDstPixelType = PixelType_Gvsp_Mono8
                stConvertParam.pDstBuffer = (c_ubyte * nConvertSize)()
                stConvertParam.nDstBufferSize = nConvertSize
                try:
                    ret = self.obj_cam.MV_CC_ConvertPixelType(stConvertParam)
                    if ret != 0:
                        message_warning('show error','convert pixel fail! ret = '+self.To_hex_str(ret))
                        continue
                    cdll.msvcrt.memcpy(byref(img_buff), stConvertParam.pDstBuffer, nConvertSize)
                    numArray = CameraOperation.Mono_numpy(self,img_buff,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
                    # self.data_signal.emit(numArray)
                except:
                    pass
                
            #如果是彩色且非RGB则转为RGB后显示
            elif  True == self.Is_color_data(self.st_frame_info.enPixelType):
                nConvertSize = self.st_frame_info.nWidth * self.st_frame_info.nHeight * 3
                stConvertParam.enDstPixelType = PixelType_Gvsp_BGR8_Packed
                stConvertParam.pDstBuffer = (c_ubyte * nConvertSize)()
                stConvertParam.nDstBufferSize = nConvertSize
                try:
                    t0 = time.time()
                    ret = self.obj_cam.MV_CC_ConvertPixelType(stConvertParam)
                    dt = time.time() - t0
                    print("MV_CC_ConvertPixelType : ",dt)
                    if ret != 0:
                        message_warning('show error','convert pixel fail! ret = '+self.To_hex_str(ret))
                        continue
                    cdll.msvcrt.memcpy(byref(img_buff), stConvertParam.pDstBuffer, nConvertSize)
                    numArray = CameraOperation.Color_numpy(self,img_buff,self.st_frame_info.nWidth,self.st_frame_info.nHeight)
                    # self.data_signal.emit(numArray)  
                except:
                    pass
            
            # dt = time.time() - t0
            # fps = 1/dt
            # cv2.putText(self.mat,"FPS : %.2f"%fps,(20,50),2,2,(0,255,0),4)
            # for obj in self.boxes:
            #     x,y,w,h = obj["box"]
            #     cv2.rectangle(self.mat,(x,y),(x+w,y+h),(0,255,0),4)
            #     cv2.putText(self.mat,obj["label"],(x,y),cv2.FONT_HERSHEY_COMPLEX,2,(0,255,0),4)

            if isinstance(self.named_window,str):
                cv2.imshow(self.named_window,self.mat)
                cv2.waitKey(10)
                if not self.bRun:
                    # self.Stop_grabbing()
                    # time.sleep(0.2)
                    # self.Close_device()
                    cv2.destroyAllWindows()
                    break
            else:
                # self.data_signal.emit(self.mat)
                time.sleep(0.02)
            if img_buff is not None:
                del img_buff
            # 
            

    def Save_jpg(self,folder=""):
        if(None == self.buf_cache):
            return
        self.buf_save_image = None
        # file_path = str(self.st_frame_info.nFrameNum) + ".jpg"
        file_path = "%s.jpg"%getStrDateTime()
        self.n_save_image_size = self.st_frame_info.nWidth * self.st_frame_info.nHeight * 3 + 2048
        if self.buf_save_image is None:
            self.buf_save_image = (c_ubyte * self.n_save_image_size)()

        stParam = MV_SAVE_IMAGE_PARAM_EX()
        stParam.enImageType = MV_Image_Jpeg;                                        # ch:需要保存的图像类型 | en:Image format to save
        stParam.enPixelType = self.st_frame_info.enPixelType                               # ch:相机对应的像素格式 | en:Camera pixel type
        stParam.nWidth      = self.st_frame_info.nWidth                                    # ch:相机对应的宽 | en:Width
        stParam.nHeight     = self.st_frame_info.nHeight                                   # ch:相机对应的高 | en:Height
        stParam.nDataLen    = self.st_frame_info.nFrameLen
        stParam.pData       = cast(self.buf_cache, POINTER(c_ubyte))
        stParam.pImageBuffer=  cast(byref(self.buf_save_image), POINTER(c_ubyte)) 
        stParam.nBufferSize = self.n_save_image_size                                 # ch:存储节点的大小 | en:Buffer node size
        stParam.nJpgQuality = 80;                                                    # ch:jpg编码，仅在保存Jpg图像时有效。保存BMP时SDK内忽略该参数
        return_code = self.obj_cam.MV_CC_SaveImageEx2(stParam)            

        if return_code != 0:
            message_warning('show error','save jpg fail! ret = '+self.To_hex_str(return_code))
            self.b_save_jpg = False
            return
        if folder:
            file_path = os.path.join(folder,file_path)
        file_open = open(file_path.encode('ascii'), 'wb+')
        img_buff = (c_ubyte * stParam.nImageLen)()
        try:
            cdll.msvcrt.memcpy(byref(img_buff), stParam.pImageBuffer, stParam.nImageLen)
            file_open.write(img_buff)
            self.b_save_jpg = False
            if(None != img_buff):
                del img_buff
            return file_path
            # tkinter.messagebox.showinfo('show info','save jpg success!')
        except:
            self.b_save_jpg = False
            message_warning("show error","get one frame failed:%s" % e.message)
            return ""
            
    def Save_Bmp(self,folder=""):
        if(0 == self.buf_cache):
            return
        self.buf_save_image = None
        # file_path = str(self.st_frame_info.nFrameNum) + ".bmp" 
        file_path = "%s.bmp"%getStrDateTime()   
        self.buf_save_image = self.st_frame_info.nWidth * self.st_frame_info.nHeight * 3 + 2048
        if self.buf_save_image is None:
            self.buf_save_image = (c_ubyte * self.n_save_image_size)()

        stParam = MV_SAVE_IMAGE_PARAM_EX()
        stParam.enImageType = MV_Image_Bmp;                                        # ch:需要保存的图像类型 | en:Image format to save
        stParam.enPixelType = self.st_frame_info.enPixelType                               # ch:相机对应的像素格式 | en:Camera pixel type
        stParam.nWidth      = self.st_frame_info.nWidth                                    # ch:相机对应的宽 | en:Width
        stParam.nHeight     = self.st_frame_info.nHeight                                   # ch:相机对应的高 | en:Height
        stParam.nDataLen    = self.st_frame_info.nFrameLen
        stParam.pData       = cast(self.buf_cache, POINTER(c_ubyte))
        stParam.pImageBuffer =  cast(byref(self.buf_save_image), POINTER(c_ubyte)) 
        stParam.nBufferSize = self.n_save_image_size                                 # ch:存储节点的大小 | en:Buffer node size
        return_code = self.obj_cam.MV_CC_SaveImageEx2(stParam)            
        if return_code != 0:
            message_warning('show error','save bmp fail! ret = '+self.To_hex_str(return_code))
            self.b_save_bmp = False
            return
        if folder:
            file_path = os.path.join(folder,file_path)
        file_open = open(file_path.encode('ascii'), 'wb+')
        img_buff = (c_ubyte * stParam.nImageLen)()
        try:
            cdll.msvcrt.memcpy(byref(img_buff), stParam.pImageBuffer, stParam.nImageLen)
            file_open.write(img_buff)
            self.b_save_bmp = False
            if(None != img_buff):
                del img_buff
            # tkinter.messagebox.showinfo('show info','save bmp success!')
            return file_path
        except:
            self.b_save_bmp = False
            # raise Exception("get one frame failed:%s" % e.message)
            message_warning("show error","get one frame failed:%s" % e.message)
            return ""
            
    def Is_mono_data(self,enGvspPixelType):
        if PixelType_Gvsp_Mono8 == enGvspPixelType or PixelType_Gvsp_Mono10 == enGvspPixelType \
            or PixelType_Gvsp_Mono10_Packed == enGvspPixelType or PixelType_Gvsp_Mono12 == enGvspPixelType \
            or PixelType_Gvsp_Mono12_Packed == enGvspPixelType:
            return True
        else:
            return False

    def Is_color_data(self,enGvspPixelType):
        if PixelType_Gvsp_BayerGR8 == enGvspPixelType or PixelType_Gvsp_BayerRG8 == enGvspPixelType \
            or PixelType_Gvsp_BayerGB8 == enGvspPixelType or PixelType_Gvsp_BayerBG8 == enGvspPixelType \
            or PixelType_Gvsp_BayerGR10 == enGvspPixelType or PixelType_Gvsp_BayerRG10 == enGvspPixelType \
            or PixelType_Gvsp_BayerGB10 == enGvspPixelType or PixelType_Gvsp_BayerBG10 == enGvspPixelType \
            or PixelType_Gvsp_BayerGR12 == enGvspPixelType or PixelType_Gvsp_BayerRG12 == enGvspPixelType \
            or PixelType_Gvsp_BayerGB12 == enGvspPixelType or PixelType_Gvsp_BayerBG12 == enGvspPixelType \
            or PixelType_Gvsp_BayerGR10_Packed == enGvspPixelType or PixelType_Gvsp_BayerRG10_Packed == enGvspPixelType \
            or PixelType_Gvsp_BayerGB10_Packed == enGvspPixelType or PixelType_Gvsp_BayerBG10_Packed == enGvspPixelType \
            or PixelType_Gvsp_BayerGR12_Packed == enGvspPixelType or PixelType_Gvsp_BayerRG12_Packed== enGvspPixelType \
            or PixelType_Gvsp_BayerGB12_Packed == enGvspPixelType or PixelType_Gvsp_BayerBG12_Packed == enGvspPixelType \
            or PixelType_Gvsp_YUV422_Packed == enGvspPixelType or PixelType_Gvsp_YUV422_YUYV_Packed == enGvspPixelType:
            return True
        else:
            return False

    def Mono_numpy(self,data,nWidth,nHeight):
        # print(type(data))
        data_ = np.frombuffer(data, count=int(nWidth * nHeight), dtype=np.uint8, offset=0)
        data_mono_arr = data_.reshape(nHeight, nWidth)
        numArray = np.zeros([nHeight, nWidth, 1],"uint8") 
        numArray[:, :, 0] = data_mono_arr
        return numArray

    def Color_numpy(self,data,nWidth,nHeight):
        # print(type(data))
        data_ = np.frombuffer(data, count=int(nWidth*nHeight*3), dtype=np.uint8, offset=0)
        data_r = data_[0:nWidth*nHeight*3:3]
        data_g = data_[1:nWidth*nHeight*3:3]
        data_b = data_[2:nWidth*nHeight*3:3]

        data_r_arr = data_r.reshape(nHeight, nWidth)
        data_g_arr = data_g.reshape(nHeight, nWidth)
        data_b_arr = data_b.reshape(nHeight, nWidth)
        numArray = np.zeros([nHeight, nWidth, 3],"uint8")

        numArray[:, :, 2] = data_r_arr
        numArray[:, :, 1] = data_g_arr
        numArray[:, :, 0] = data_b_arr
        return numArray
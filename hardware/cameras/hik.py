from hardware.cameras.base_camera import *
from hardware.cameras.MVSImport.MvCameraControl_class import *
from hardware.cameras.MVSImport.CamOperation_class import *
import cv2

class HIK(BaseCamera):
    def __init__(self, config=None) -> None:
        self._stFrameInfo = MV_FRAME_OUT_INFO_EX()
        super().__init__(config=config)

    def set_config(self, config):
        print("Set camera config")
        self._config = config
        self.create_device()

    def get_config(self):
        return self._config
    
    def get_error(self) -> str:
        return self._error
    
    @staticmethod
    def get_devices() -> dict:
        devices, _ = HIK.get_devices_and_list_devinfo()
        return devices
    
    @staticmethod
    def get_devices_and_list_devinfo() -> tuple:
        list_devinfo = MV_CC_DEVICE_INFO_LIST()
        tlayerType = MV_GIGE_DEVICE | MV_USB_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayerType, list_devinfo)
        devices = {}
        for i in range(0, list_devinfo.nDeviceNum):
            mvcc_dev_info = cast(list_devinfo.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
            if mvcc_dev_info.nTLayerType == MV_GIGE_DEVICE:
                strModeName = ""
                for per in mvcc_dev_info.SpecialInfo.stGigEInfo.chModelName:
                    strModeName = strModeName + chr(per)

                strSerialNumber = ""
                for per in mvcc_dev_info.SpecialInfo.stGigEInfo.chSerialNumber:
                    if per == 0:
                        break
                    strSerialNumber = strSerialNumber + chr(per)

                nip1 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0xff000000) >> 24)
                nip2 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x00ff0000) >> 16)
                nip3 = ((mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x0000ff00) >> 8)
                nip4 = (mvcc_dev_info.SpecialInfo.stGigEInfo.nCurrentIp & 0x000000ff)
                name = "Gige["+str(i)+"]:"+str(nip1)+"."+str(nip2)+"."+str(nip3)+"."+str(nip4)
                devices[strSerialNumber] = name
            elif mvcc_dev_info.nTLayerType == MV_USB_DEVICE:
                strModeName = ""
                for per in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chModelName:
                    if per == 0:
                        break
                    strModeName = strModeName + chr(per)

                strSerialNumber = ""
                for per in mvcc_dev_info.SpecialInfo.stUsb3VInfo.chSerialNumber:
                    if per == 0:
                        break
                    strSerialNumber = strSerialNumber + chr(per)
                devices[strSerialNumber] = strModeName
        return devices, list_devinfo
    
    def create_device(self):
        devices, devices_info = HIK.get_devices_and_list_devinfo()
        if not devices:
            self._error = ERR_NOT_FOUND_DEVICE
            self._cap = None
            return
        
        if self._config is None:
            self._error = ERR_CONFIG_IS_NONE
            self._cap = None
            self._model_name = ""
            return

        serinumber = self._config.get("id", 0)
        sn_keys = list(devices.keys())
        if serinumber in devices:
            index = sn_keys.index(serinumber)
        else:
            index = None

        if index is not None:
            self._cap = CameraOperation(MvCamera(), devices_info, index)
            self._model_name = devices[serinumber]
        else:
            self._cap = CameraOperation(MvCamera(), devices_info, 0)
            self._model_name = devices[sn_keys[0]]
        
        if not self.is_valid_model_name():
            self._error = ERR_MODEL_NAME
            self._cap = None
        else:
            self._error = NO_ERROR

    def open(self) -> bool:
        if self._cap is None:
            return False
        
        _is_open = True
        self._error = NO_ERROR

        try:
            ret = self._cap.Open_device()
            if 0 != ret:
                _is_open = False
            else:
                feature = self._config.get("feature", None)
                if feature:
                    ret = self._cap.obj_cam.MV_CC_FeatureLoad(feature)
                    if 0 != ret:
                        self._error = ERR_LOAD_FEATURE_FAIL
        except Exception as ex:
            _is_open = False
            self._error = str(ex)
        return _is_open
    
    def close(self) -> bool:
        try:
            self._cap.Close_device()
            return True
        except:
            return False
        
    def start_grabbing(self) -> bool:
        try:
            self._cap.Start_grabbing()
            return True
        except:
            return False
        
    def stop_grabbing(self) -> bool:
        try:
            self._cap.Stop_grabbing()
            return True
        except:
            return False
        
    def grab(self):
        _mat = None
        try:
            ret = self._cap.obj_cam.MV_CC_GetOneFrameTimeout(byref(self._cap.buf_cache), self._cap.n_payload_size, self._stFrameInfo, 1000)
            if ret == 0:
                img_buff = None
                self._cap.st_frame_info = self._stFrameInfo
                self._cap.n_save_image_size = self._cap.st_frame_info.nWidth * self._cap.st_frame_info.nHeight * 3 + 2048
                if img_buff is None:
                    img_buff = (c_ubyte * self._cap.n_save_image_size)()
        
                if PixelType_Gvsp_Mono8 == self._cap.st_frame_info.enPixelType:
                    _mat = CameraOperation.Mono_numpy(self._cap,self._cap.buf_cache,self._cap.st_frame_info.nWidth,self._cap.st_frame_info.nHeight)
                
                elif PixelType_Gvsp_BayerGB8 == self._cap.st_frame_info.enPixelType:
                    numArray = CameraOperation.Mono_numpy(self._cap,self._cap.buf_cache,self._cap.st_frame_info.nWidth,self._cap.st_frame_info.nHeight)
                    _mat = cv2.cvtColor(numArray,cv2.COLOR_BAYER_GB2RGB)
                
                elif PixelType_Gvsp_BayerRG8 == self._cap.st_frame_info.enPixelType:
                    numArray = CameraOperation.Mono_numpy(self._cap,self._cap.buf_cache,self._cap.st_frame_info.nWidth,self._cap.st_frame_info.nHeight)
                    _mat = cv2.cvtColor(numArray,cv2.COLOR_BAYER_RG2RGB)

                elif PixelType_Gvsp_RGB8_Packed == self._cap.st_frame_info.enPixelType:
                    _mat = CameraOperation.Color_numpy(self._cap,self._cap.buf_cache,self._cap.st_frame_info.nWidth,self._cap.st_frame_info.nHeight)
        except Exception as e:
            self._error = str(e)
            
        return self._error, _mat

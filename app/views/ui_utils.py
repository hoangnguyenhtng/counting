
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtCore import *
import os
import time
import cv2

def load_style_sheet(filename: str, wd: QWidget):
    with open(filename, "r") as file:
        style = file.read()
        wd.setStyleSheet(style)
        file.close()

def newLabel(text, style="", align=None):
    lb = QLabel(text)
    if style:
        lb.setStyleSheet(style)
    if align:
        lb.setAlignment(align)
    return lb

def newTabWidget(parent=None,position=QTabWidget.North):
    tab = QTabWidget(parent)
    tab.setTabPosition(position)
    # tab.setMovable(True)
    return tab

def addTabs(tab,widgets,names,icons=None):
    if icons is not None:
        for w,name,ico in zip(widgets,names,icons):
            if isinstance(tab, QTabWidget):
                tab.addTab(w,newIcon(ico),name)
            elif isinstance(tab, QToolBox):
                tab.addItem(w,newIcon(ico),name)
    else:
        for w,name in zip(widgets,names):
            if isinstance(tab, QTabWidget):
                tab.addTab(w,name)
            elif isinstance(tab, QToolBox):
                tab.addItem(w,name)

def add_context_menu(parent,widget,actions, popup_function=None):
    menu = QMenu(parent)
    addActions(menu,actions)
    widget.setContextMenuPolicy(Qt.CustomContextMenu)
    if popup_function is None:
        widget.customContextMenuRequested.connect(lambda: menu.exec_(QCursor.pos()))
    else:
        widget.customContextMenuRequested.connect(popup_function)
    return menu

class BoxEditLabel(QDialog):
    def __init__(self,title="QDialog",parent=None):
        super(BoxEditLabel,self).__init__(parent)
        self.setObjectName("roi_name_dialog")
        self.setWindowTitle(title)
        layout = QVBoxLayout()

        bb = newBB(self)

        self.ln_name = QLineEdit()
        self.ln_name.setObjectName("roi_name_input")
        self.ln_name.setFocus()
        self.list_name = QListWidget()
        self.list_name.setObjectName("roi_name_history")
        addWidgets(layout,[self.ln_name, bb, self.list_name])
        self.setLayout(layout)

        self.list_name.itemClicked.connect(self.itemClicked)
        self.list_name.itemDoubleClicked.connect(self.itemDoubleClicked)

    def itemClicked(self,item):
        self.ln_name.setText(item.text())
        pass

    def itemDoubleClicked(self,item):
        self.ln_name.setText(item.text())
        self.accept()
        pass

    def popUp(self,text="",names=[],bMove=False):
        self.list_name.clear()
        self.list_name.addItems(names)
        self.ln_name.setText(text)
        self.ln_name.setSelection(0,len(text))
        if bMove:
            self.move(QCursor.pos())
        return self.ln_name.text() if self.exec_() else ""

def create_resources():
    cwd = os.getcwd()
    folder = "resources/icon"
    files = os.listdir(folder)
    top =["<!DOCTYPE RCC><RCC version=\"1.0\">","<qresource>"]
    bot = ["</qresource>","</RCC>"]
    for f in files:
        base, ext = os.path.splitext(f)
        alias = f"<file alias=\"%s\">icon/%s</file>"%(base,f)
        top = top + [alias]
    resources = "\n".join(top + bot)
    with open("resources/resources.qrc","w") as ff:
        ff.write(resources)
        ff.close()
    
    os.system("pyrcc5 -o libs/resources.py resources/resources.qrc")

def add_dock(parent:QMainWindow, text, object_name, widget, area=Qt.RightDockWidgetArea,
             feature=QDockWidget.NoDockWidgetFeatures, orient=None):
    dock = QDockWidget(text, parent)
    dock.setObjectName(object_name)
    dock.setAllowedAreas(Qt.AllDockWidgetAreas)
    dock.setFeatures(feature)
    dock.setWidget(widget)
    if orient is not None:
        parent.addDockWidget(area, dock, orient)
    else:
        parent.addDockWidget(area, dock)
    return dock

def add_scroll(widget):
    scroll = QScrollArea()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    return scroll

def get_save_file_name_dialog(parent,base="", _filter_="Image files (*png *jpg *bmp)"):
    options = QFileDialog.Options()
    # options |= QFileDialog.DontUseNativeDialog
    filename,_ = QFileDialog.getSaveFileName(parent,"Seve as",base, _filter_,options=options)
    return filename

def get_folder_name_dialog(parent,base=""):
    options = QFileDialog.Options()
    # options |= QFileDialog.DontUseNativeDialog
    options |= QFileDialog.ShowDirsOnly
    folder 	= QFileDialog.getExistingDirectory(parent,"Select folder",base,options=options)
    return folder

def get_file_name_dialog(parent,base="",_filter_="Image files (*png *jpg *bmp)"):
    options = QFileDialog.Options()
    # options |= QFileDialog.DontUseNativeDialog
    filename,_ = QFileDialog.getOpenFileName(parent,"Select file",base
            ,_filter_,options=options)
    return filename

class WindowMixin(object):
    def menu(self, title, actions=None)->QMenu:
        menu = self.menuBar().addMenu(title)
        if actions:
            addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None,orient=Qt.TopToolBarArea):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            addActions(toolbar, actions)
        self.addToolBar(orient, toolbar)
        return toolbar

class ToolBar(QToolBar):
    def __init__(self, title):
        super(ToolBar, self).__init__(title)
        layout = self.layout()
        m = (0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setContentsMargins(*m)
        self.setContentsMargins(*m)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

    def addAction(self, action):
        if isinstance(action, QWidgetAction):
            return super(ToolBar, self).addAction(action)
        btn = ToolButton()
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(self.toolButtonStyle())
        self.addWidget(btn)


class ToolButton(QToolButton):
    """ToolBar companion class which ensures all buttons have the same size."""
    minSize = (70, 40)
    def minimumSizeHint(self):
        ms = super(ToolButton, self).minimumSizeHint()
        w1, h1 = ms.width(), ms.height()
        w2, h2 = self.minSize
        ToolButton.minSize = max(w1, w2), max(h1, h2)
        return QSize(*ToolButton.minSize)

class struct(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

def newBB(parent):
    BB = QDialogButtonBox
    bb = BB(BB.Ok|BB.Cancel)
    bb.rejected.connect(parent.reject)
    bb.accepted.connect(parent.accept) 
    return bb

def newToolButton(action, parent=None, style=Qt.ToolButtonTextUnderIcon):
    b = QToolButton(parent)
    b.setToolButtonStyle(style)
    b.setDefaultAction(action)
    return b

def newDialogButton(parent,texts,slots,icons,orient=Qt.Vertical):
    bb = QDialogButtonBox(orient, parent)
    for txt,slot,icon in zip(texts,slots,icons):
        but = bb.addButton("", QDialogButtonBox.ApplyRole)
        but.setToolTip(txt)
        but.setProperty("class", "DialogButton")
        if slot is not None:
            but.clicked.connect(slot)
        if icon is not None:
            but.setIcon(newIcon(icon))
    return bb

def newRadioButton(text,slot=None, state=False):
    rad = QRadioButton(text)
    if slot is not None:
        rad.clicked.connect(slot)
    rad.setChecked(state)
    return rad

def newSlider(_range=(0,255),value=0,step=1,slot=None):
    sl = QSlider(Qt.Horizontal)
    a,b = _range
    sl.setRange(a,b)
    sl.setValue(value)
    sl.setSingleStep(step)
    if slot is not None:
        sl.valueChanged.connect(slot)
    return sl

def newDoubleSpinbox(range_,value,step=1,slot=None):
    sp = QDoubleSpinBox()
    sp.setValue(value)
    a,b = range_
    sp.setRange(a,b)
    sp.setSingleStep(step)
    if slot:
        sp.valueChanged.connect(slot)
    return sp

def newSpinbox(range_,value,step=1,slot=None):
    sp = QSpinBox()
    a,b = range_
    sp.setRange(a,b)
    sp.setValue(value)
    sp.setSingleStep(step)
    if slot:
        sp.valueChanged.connect(slot)
    return sp

def newCheckBox(text, slot=None, state=False, tooltip=""):
    ch = QCheckBox(text)
    ch.setChecked(state)
    if slot is not None:
        ch.stateChanged.connect(slot)
    if tooltip:
        ch.setToolTip(tooltip)
    return ch
    
def newIcon(icon):
    return QIcon(':/' + icon)

def addActions(menu,actions):
    for act in actions:
        if isinstance(act,QAction):
            menu.addAction(act)
        else:
            menu.addMenu(act)

def newCbb(items, parent=None, slot=None):
    cbb = QComboBox(parent)
    [cbb.addItem(str(item)) for item in items]
    if slot is not None:
        cbb.currentIndexChanged.connect(slot)
    return cbb

def newButton(text, parent=None, slot=None,icon=None,enabled=True):
    b = QPushButton(text, parent)
    if slot is not None:
        b.clicked.connect(slot)
    if icon is not None:
        b.setIcon(newIcon(icon))
    b.setEnabled(enabled)
    return b

def new_hlayout(widgets=[], stretchs=[], parent=None):
    h = QHBoxLayout(parent)
    addWidgets(h, widgets, stretchs)
    return h

def new_vlayout(widgets=[], stretchs=[], parent=None):
    h = QVBoxLayout(parent)
    addWidgets(h, widgets, stretchs)
    return h

def addWidgets(layout,wds,stretchs=[]):
    for i,w in enumerate(wds):
        if isinstance(w, QWidget):   
            layout.addWidget(w)
        else:
            layout.addLayout(w)
        if stretchs:
            if isinstance(layout, QSplitter):
                layout.setStretchFactor(i,stretchs[i])
            else:
                layout.setStretch(i,stretchs[i])

def addTriggered(action,trigger):
    action.triggered.connect(trigger)

def newAction(parent,text,slot=None,shortcut=None,icon=None,tooltip=None,enabled=True):
    a = QAction(text,parent)
    if icon is not None:
        a.setIcon(newIcon(icon))
    if shortcut is not None:
        a.setShortcut(shortcut)
    if slot is not None:
        a.triggered.connect(slot)
    if tooltip is not None:
        a.setToolTip(tooltip)
    a.setEnabled(enabled)
    return a

class ListWidget(QListWidget):
    def __init__(self,style=None,parent=None):
        super(ListWidget,self).__init__(parent)
        if style is not None:
            self.setStyleSheet(style)

        clear = newAction(self,"clear",self.clear,"ctrl+x")
        self.menu = add_context_menu(self,self,[clear])

    def addLog(self, log, color=None, reverse=False):
        if self.count() > 1000:
            self.clear()
        log = "%s : %s"%(time.strftime("%H:%M:%S"), str(log))
        if reverse:
            self.insertItem(0,log)
            if color is not None:
                self.item(0).setForeground(color)
        else:
            self.addItem(log)
            n = self.count()
            if color is not None:
                self.item(n-1).setForeground(color)

def ndarray2pixmap(arr):
    if len(arr.shape) == 2:
        rgb = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    h, w, channel = rgb.shape
    qimage = QImage(rgb.data, w, h, channel*w, QImage.Format_RGB888)
    pixmap = QPixmap.fromImage(qimage)
    return pixmap

if __name__ == "__main__":
    create_resources()
    pass


    
    

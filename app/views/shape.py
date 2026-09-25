from PyQt5.QtGui import *
from PyQt5.QtCore import *

import numpy as np


# color shape
DEFAULT_FILL_COLOR = QColor(128, 128, 255, 100)
DEFAULT_SELECT_FILL_COLOR = QColor(255, 100,100, 50)
DEFAULT_VISIBLE_FILL_COLOR = QColor(128, 128, 0, 0)
DEFAULT_VERTEX_FILL_COLOR = QColor(255, 255, 255, 0)
DEFAULT_VERTEX_SELECT_FILL_COLOR = QColor(255,255,0, 255)
DEFAULT_SELECT_COLOR = QColor(255,0,0, 255)


class Shape(object):
    RADIUS = 20
    THICKNESS = 3
    FONT_SIZE = 25
    MIN_WIDTH = 10
    def __init__(self, label=None, name=None):
        super(Shape,self).__init__()
        self.points = []
        self.selected = False
        self.visible  = False
        self.corner   = None
        self.label    = label if label is not None else name
        self.image_debug = None
        # shape parameter 
        self.config   = None
        self.scale = 1.
        self._b_lock = False
        self._b_hide = False
        # 
        self.vertex_fill_color        = DEFAULT_VERTEX_FILL_COLOR
        self.vertex_select_fill_color = DEFAULT_VERTEX_SELECT_FILL_COLOR
        self.fill_color               = DEFAULT_FILL_COLOR
        self.select_color             = DEFAULT_SELECT_COLOR
        self.select_fill_color        = DEFAULT_SELECT_FILL_COLOR
        self.visible_fill_color       = DEFAULT_VISIBLE_FILL_COLOR

    @property
    def name(self):
        """Compatibility alias used by the configuration UI."""
        return self.label

    @name.setter
    def name(self, value):
        self.label = value

    def addPoint(self, x, y=None):
        """Append a point from either a QPointF or an x/y coordinate pair."""
        point = QPointF(x) if y is None else QPointF(float(x), float(y))
        self.points.append(point)

    @property
    def lock(self):
        return self._b_lock
    
    @lock.setter
    def lock(self, val:bool):
        self._b_lock = val

    @property
    def hide(self):
        return self._b_hide
    
    @hide.setter
    def hide(self, val:bool):
        self._b_hide = val
    
    def drawVertex(self, path, i):
        d = max(int(Shape.RADIUS / (self.scale+1e-5)), 10)
        point = self.points[i]
        if self.corner is not None and i == self.corner:
            path.addRect(point.x() - d, point.y() - d, 2*d, 2*d)
        elif self.visible:
            path.addEllipse(point, d/2, d/2)
        else:
            path.addEllipse(point, d / 2.0, d / 2.0)
    
    def paint(self,painter,s=1):
        if self.hide:
            return
        self.scale = s
        color = Qt.green
        lw = max(int(Shape.THICKNESS / (self.scale+1e-5)), 1)
        painter.setPen(QPen(color, lw))
        line_path = QPainterPath()
        vertex_path = QPainterPath()

        line_path.moveTo(self.points[0])

        for i, p in enumerate(self.points):
            line_path.lineTo(p)
            self.drawVertex(vertex_path,i)

        line_path.lineTo(self.points[0])
        #  draw rect
        painter.drawPath(line_path)
        # draw label
        if self.label is not None:
            fs = max(int(Shape.FONT_SIZE / (self.scale+1e-5)), 20)
            font = QFont("Arial", fs)
            painter.setFont(font)
            painter.drawText(int(self[0].x()-1), int(self[0].y()-1), self.label)

        # Draw coordinate labels for each point if selected
        if self.selected:
            fs_coord = max(int(14 / (self.scale + 1e-5)), 10)
            font_coord = QFont("Consolas", fs_coord)
            painter.setFont(font_coord)
            painter.setPen(QPen(QColor(250, 204, 21), 1))  # sleek yellow/amber color
            for i, p in enumerate(self.points):
                pt_text = f"P{i}: ({int(p.x())}, {int(p.y())})"
                if hasattr(self, 'homography_matrix') and self.homography_matrix is not None:
                    try:
                        from app.core.geometry import pixel_to_world
                        wx, wy = pixel_to_world(self.homography_matrix, (p.x(), p.y()))
                        pt_text += f" [{wx:.1f}, {wy:.1f}]mm"
                    except Exception:
                        pass
                painter.drawText(int(p.x() + 12), int(p.y() - 12), pt_text)

        
        #  fill
        color = self.vertex_select_fill_color if (self.visible or self.corner is not None) else self.vertex_fill_color
        color = self.select_color if self.selected else color
        painter.fillPath(vertex_path, color)

        # color = self.visible_fill_color if (self.visible or self.corner is not None) else self.fill_color
        # color = self.select_fill_color if self.selected else color
        # painter.fillPath(line_path, color)

    def translate_(self,v:QPointF):
        self.points = [p + v for p in self.points]
    
    def move(self,v:QPointF):
        if not self.lock:
            self.points = [p + v for p in self.points]
        pass
     
    def copy(self):
        shape = Shape(label=self.label+"_copy")
        shape.points = self.points
        shape.visible = self.visible
        shape.corner = self.corner
        shape.config = self.config
        shape.translate_(QPointF(50.,50.))
        return shape

    def contain(self,pos):
        x,y = pos.x() , pos.y()
        tl = self.points[0]
        br = self.points[2]
        x1,y1 = tl.x() , tl.y()
        x2,y2 = br.x() , br.y()
        return x1 < x < x2 and y1 < y < y2

    def get_corner(self,pos,epsilon=10):
        for i in range(len(self.points)):
            d = self.distance(pos,self.points[i])
            if d < epsilon:
                self.corner = i
                return True
        self.corner = None
        return False

    def distance(self,p1,p2):
        p = p2 - p1
        return np.sqrt(p.x()**2+p.y()**2)

    def dis_to(self,pos):
        x,y = pos.x() , pos.y()
        tl = self.points[0]
        br = self.points[2]
        dx = min([np.abs(x - tl.x()),np.abs(x - br.x())])
        dy = min([np.abs(y - tl.y()),np.abs(y - br.y())])
        if self.contain(pos):
            return min(dx,dy)
        else:
            return -min(dx,dy)

    def change(self,v):
        if self.lock:
            return
        points = self.points
        corner = self.corner
        R = QRectF(self.points[0],self.points[2])
        pos = self.points[corner] + v

        if corner == 0:
            R.setTopLeft(pos)
        elif corner == 1:
            R.setTopRight(pos)
        elif corner == 2:
            R.setBottomRight(pos)
        elif corner == 3:
            R.setBottomLeft(pos)
        
        ret, points = self.get_points(R)
        if ret:
            self.points = points

    def get_points(self,r = QRectF()):
        pos1 = r.topLeft()
        pos2 = r.topRight()
        pos3 = r.bottomRight()
        pos4 = r.bottomLeft()

        width = pos3.x() - pos1.x()
        height = pos3.y() - pos1.y()

        if width > Shape.MIN_WIDTH and height > Shape.MIN_WIDTH:
            ret = True
        else:
            ret = False
        return ret, [pos1,pos2,pos3,pos4]    

    @property
    def cvBox(self):
        tl = self.points[0]
        br = self.points[2]
        x, y = tl.x(), tl.y()
        w, h = br.x() - x, br.y() - y

        x, y, w, h = list(map(int, [x, y, w, h]))
        x = max(x, 0)
        y = max(y, 0)
        return [x, y, w, h]
    
    def __len__(self):
        return len(self.points)

    def __getitem__(self, key):
        return self.points[key]

    def __setitem__(self, key, value):
        self.points[key] = value


if __name__ == "__main__":
    pass

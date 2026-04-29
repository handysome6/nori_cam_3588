# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'auto_gui.ui'
##
## Created by: Qt User Interface Compiler version 6.9.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QSizePolicy, QStatusBar, QToolBar,
    QVBoxLayout, QWidget)

class Ui_MainWIndow(object):
    def setupUi(self, MainWIndow):
        if not MainWIndow.objectName():
            MainWIndow.setObjectName(u"MainWIndow")
        MainWIndow.resize(1044, 626)
        self.actionConnnect_Cameras = QAction(MainWIndow)
        self.actionConnnect_Cameras.setObjectName(u"actionConnnect_Cameras")
        self.actionConnnect_Cameras.setMenuRole(QAction.MenuRole.NoRole)
        self.actionCapture_Camera = QAction(MainWIndow)
        self.actionCapture_Camera.setObjectName(u"actionCapture_Camera")
        self.actionCapture_Camera.setMenuRole(QAction.MenuRole.NoRole)
        self.centralwidget = QWidget(MainWIndow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.verticalLayout_4 = QVBoxLayout(self.centralwidget)
        self.verticalLayout_4.setObjectName(u"verticalLayout_4")
        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.graphicsView_left = QGraphicsView(self.centralwidget)
        self.graphicsView_left.setObjectName(u"graphicsView_left")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.graphicsView_left.sizePolicy().hasHeightForWidth())
        self.graphicsView_left.setSizePolicy(sizePolicy)

        self.horizontalLayout_2.addWidget(self.graphicsView_left)

        self.graphicsView_right = QGraphicsView(self.centralwidget)
        self.graphicsView_right.setObjectName(u"graphicsView_right")
        sizePolicy.setHeightForWidth(self.graphicsView_right.sizePolicy().hasHeightForWidth())
        self.graphicsView_right.setSizePolicy(sizePolicy)

        self.horizontalLayout_2.addWidget(self.graphicsView_right)


        self.verticalLayout_4.addLayout(self.horizontalLayout_2)

        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.verticalLayout_3 = QVBoxLayout()
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.groupBox_2 = QGroupBox(self.centralwidget)
        self.groupBox_2.setObjectName(u"groupBox_2")
        self.verticalLayout_2 = QVBoxLayout(self.groupBox_2)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.horizontalLayout_3 = QHBoxLayout()
        self.horizontalLayout_3.setObjectName(u"horizontalLayout_3")
        self.label_6 = QLabel(self.groupBox_2)
        self.label_6.setObjectName(u"label_6")

        self.horizontalLayout_3.addWidget(self.label_6)

        self.lineEdit_savingPath = QLineEdit(self.groupBox_2)
        self.lineEdit_savingPath.setObjectName(u"lineEdit_savingPath")

        self.horizontalLayout_3.addWidget(self.lineEdit_savingPath)

        self.pushButton_selectFolder = QPushButton(self.groupBox_2)
        self.pushButton_selectFolder.setObjectName(u"pushButton_selectFolder")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.pushButton_selectFolder.sizePolicy().hasHeightForWidth())
        self.pushButton_selectFolder.setSizePolicy(sizePolicy1)

        self.horizontalLayout_3.addWidget(self.pushButton_selectFolder)


        self.verticalLayout_2.addLayout(self.horizontalLayout_3)

        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.label_7 = QLabel(self.groupBox_2)
        self.label_7.setObjectName(u"label_7")

        self.horizontalLayout.addWidget(self.label_7)

        self.lineEdit_expTime = QLineEdit(self.groupBox_2)
        self.lineEdit_expTime.setObjectName(u"lineEdit_expTime")

        self.horizontalLayout.addWidget(self.lineEdit_expTime)

        self.label_8 = QLabel(self.groupBox_2)
        self.label_8.setObjectName(u"label_8")

        self.horizontalLayout.addWidget(self.label_8)

        self.lineEdit_gain = QLineEdit(self.groupBox_2)
        self.lineEdit_gain.setObjectName(u"lineEdit_gain")

        self.horizontalLayout.addWidget(self.lineEdit_gain)


        self.verticalLayout_2.addLayout(self.horizontalLayout)


        self.verticalLayout_3.addWidget(self.groupBox_2)

        self.groupBox = QGroupBox(self.centralwidget)
        self.groupBox.setObjectName(u"groupBox")
        self.gridLayout = QGridLayout(self.groupBox)
        self.gridLayout.setObjectName(u"gridLayout")
        self.label = QLabel(self.groupBox)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.lineEdit_serialPort = QLineEdit(self.groupBox)
        self.lineEdit_serialPort.setObjectName(u"lineEdit_serialPort")

        self.gridLayout.addWidget(self.lineEdit_serialPort, 0, 1, 1, 3)

        self.label_2 = QLabel(self.groupBox)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.lineEdit_hFov = QLineEdit(self.groupBox)
        self.lineEdit_hFov.setObjectName(u"lineEdit_hFov")

        self.gridLayout.addWidget(self.lineEdit_hFov, 1, 1, 1, 1)

        self.label_4 = QLabel(self.groupBox)
        self.label_4.setObjectName(u"label_4")

        self.gridLayout.addWidget(self.label_4, 1, 2, 1, 1)

        self.lineEdit_hCount = QLineEdit(self.groupBox)
        self.lineEdit_hCount.setObjectName(u"lineEdit_hCount")

        self.gridLayout.addWidget(self.lineEdit_hCount, 1, 3, 1, 1)

        self.label_3 = QLabel(self.groupBox)
        self.label_3.setObjectName(u"label_3")

        self.gridLayout.addWidget(self.label_3, 2, 0, 1, 1)

        self.lineEdit_vFov = QLineEdit(self.groupBox)
        self.lineEdit_vFov.setObjectName(u"lineEdit_vFov")

        self.gridLayout.addWidget(self.lineEdit_vFov, 2, 1, 1, 1)

        self.label_5 = QLabel(self.groupBox)
        self.label_5.setObjectName(u"label_5")

        self.gridLayout.addWidget(self.label_5, 2, 2, 1, 1)

        self.lineEdit_vCount = QLineEdit(self.groupBox)
        self.lineEdit_vCount.setObjectName(u"lineEdit_vCount")

        self.gridLayout.addWidget(self.lineEdit_vCount, 2, 3, 1, 1)


        self.verticalLayout_3.addWidget(self.groupBox)


        self.horizontalLayout_4.addLayout(self.verticalLayout_3)

        self.gridLayout_2 = QGridLayout()
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.pushButton_botRight = QPushButton(self.centralwidget)
        self.pushButton_botRight.setObjectName(u"pushButton_botRight")
        sizePolicy1.setHeightForWidth(self.pushButton_botRight.sizePolicy().hasHeightForWidth())
        self.pushButton_botRight.setSizePolicy(sizePolicy1)

        self.gridLayout_2.addWidget(self.pushButton_botRight, 1, 1, 1, 1)

        self.pushButton_topRight = QPushButton(self.centralwidget)
        self.pushButton_topRight.setObjectName(u"pushButton_topRight")
        sizePolicy1.setHeightForWidth(self.pushButton_topRight.sizePolicy().hasHeightForWidth())
        self.pushButton_topRight.setSizePolicy(sizePolicy1)

        self.gridLayout_2.addWidget(self.pushButton_topRight, 0, 1, 1, 1)

        self.pushButton_botLeft = QPushButton(self.centralwidget)
        self.pushButton_botLeft.setObjectName(u"pushButton_botLeft")
        sizePolicy1.setHeightForWidth(self.pushButton_botLeft.sizePolicy().hasHeightForWidth())
        self.pushButton_botLeft.setSizePolicy(sizePolicy1)

        self.gridLayout_2.addWidget(self.pushButton_botLeft, 1, 0, 1, 1)

        self.pushButton_topLeft = QPushButton(self.centralwidget)
        self.pushButton_topLeft.setObjectName(u"pushButton_topLeft")
        sizePolicy1.setHeightForWidth(self.pushButton_topLeft.sizePolicy().hasHeightForWidth())
        self.pushButton_topLeft.setSizePolicy(sizePolicy1)

        self.gridLayout_2.addWidget(self.pushButton_topLeft, 0, 0, 1, 1)


        self.horizontalLayout_4.addLayout(self.gridLayout_2)

        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.pushButton_start = QPushButton(self.centralwidget)
        self.pushButton_start.setObjectName(u"pushButton_start")
        sizePolicy1.setHeightForWidth(self.pushButton_start.sizePolicy().hasHeightForWidth())
        self.pushButton_start.setSizePolicy(sizePolicy1)

        self.verticalLayout.addWidget(self.pushButton_start)

        self.pushButton_stop = QPushButton(self.centralwidget)
        self.pushButton_stop.setObjectName(u"pushButton_stop")
        sizePolicy1.setHeightForWidth(self.pushButton_stop.sizePolicy().hasHeightForWidth())
        self.pushButton_stop.setSizePolicy(sizePolicy1)

        self.verticalLayout.addWidget(self.pushButton_stop)


        self.horizontalLayout_4.addLayout(self.verticalLayout)


        self.verticalLayout_4.addLayout(self.horizontalLayout_4)

        MainWIndow.setCentralWidget(self.centralwidget)
        self.statusbar = QStatusBar(MainWIndow)
        self.statusbar.setObjectName(u"statusbar")
        MainWIndow.setStatusBar(self.statusbar)
        self.toolBar = QToolBar(MainWIndow)
        self.toolBar.setObjectName(u"toolBar")
        MainWIndow.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolBar)

        self.toolBar.addAction(self.actionConnnect_Cameras)
        self.toolBar.addAction(self.actionCapture_Camera)

        self.retranslateUi(MainWIndow)

        QMetaObject.connectSlotsByName(MainWIndow)
    # setupUi

    def retranslateUi(self, MainWIndow):
        MainWIndow.setWindowTitle(QCoreApplication.translate("MainWIndow", u"Nori Auto PTS Calib Cam (RK3588)", None))
        self.actionConnnect_Cameras.setText(QCoreApplication.translate("MainWIndow", u"Connnect Cameras", None))
        self.actionCapture_Camera.setText(QCoreApplication.translate("MainWIndow", u"Capture Camera", None))
#if QT_CONFIG(tooltip)
        self.actionCapture_Camera.setToolTip(QCoreApplication.translate("MainWIndow", u"Capture Camera Once", None))
#endif // QT_CONFIG(tooltip)
        self.groupBox_2.setTitle(QCoreApplication.translate("MainWIndow", u"Camera Setting", None))
        self.label_6.setText(QCoreApplication.translate("MainWIndow", u"Image Saving Path", None))
        self.lineEdit_savingPath.setText("")
        self.pushButton_selectFolder.setText(QCoreApplication.translate("MainWIndow", u"Select Folder", None))
        self.label_7.setText(QCoreApplication.translate("MainWIndow", u"Exposure Time", None))
        self.lineEdit_expTime.setText(QCoreApplication.translate("MainWIndow", u"90000", None))
        self.label_8.setText(QCoreApplication.translate("MainWIndow", u"Gain", None))
        self.lineEdit_gain.setText(QCoreApplication.translate("MainWIndow", u"8", None))
        self.groupBox.setTitle(QCoreApplication.translate("MainWIndow", u"PTS Setting", None))
        self.label.setText(QCoreApplication.translate("MainWIndow", u"Serial port:", None))
        self.lineEdit_serialPort.setText(QCoreApplication.translate("MainWIndow", u"COM4", None))
        self.label_2.setText(QCoreApplication.translate("MainWIndow", u"HFOV", None))
        self.lineEdit_hFov.setText(QCoreApplication.translate("MainWIndow", u"60", None))
        self.label_4.setText(QCoreApplication.translate("MainWIndow", u"H Count", None))
        self.lineEdit_hCount.setText(QCoreApplication.translate("MainWIndow", u"10", None))
        self.label_3.setText(QCoreApplication.translate("MainWIndow", u"VFOV", None))
        self.lineEdit_vFov.setText(QCoreApplication.translate("MainWIndow", u"40", None))
        self.label_5.setText(QCoreApplication.translate("MainWIndow", u"V Count", None))
        self.lineEdit_vCount.setText(QCoreApplication.translate("MainWIndow", u"10", None))
        self.pushButton_botRight.setText(QCoreApplication.translate("MainWIndow", u"Bot Right", None))
        self.pushButton_topRight.setText(QCoreApplication.translate("MainWIndow", u"Top RIght", None))
        self.pushButton_botLeft.setText(QCoreApplication.translate("MainWIndow", u"Bot Left", None))
        self.pushButton_topLeft.setText(QCoreApplication.translate("MainWIndow", u"Top Left", None))
        self.pushButton_start.setText(QCoreApplication.translate("MainWIndow", u"Start", None))
        self.pushButton_stop.setText(QCoreApplication.translate("MainWIndow", u"Stop", None))
        self.toolBar.setWindowTitle(QCoreApplication.translate("MainWIndow", u"toolBar", None))
    # retranslateUi


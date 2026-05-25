# 车身Tecnomatix仿真后台数据存放规范

| 车身Tecnomatix仿真后台数据存放规范 |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- |
| 层级1 | 层级2 | 层级3 | 层级4 | 层级5 | 层级6 | 层级7 |
| Sysroot
后台根目录 | Images
工艺截图 |  |  |  |  |  |
|  | TuneCells
RobCadStudy数据 |  |  |  |  |  |
|  | Customization
客户化定制文件 |  |  |  |  |  |
|  | xmlFiles
备份或导出的数据 |  |  |  |  |  |
|  | RobotsMachineDataFiles
机器人数据 |  |  |  |  |  |
|  | Volumes
机器人轨迹包络数据 |  |  |  |  |  |
|  | LibraryRoot
存放3D文件 | Standard_Library
标准资源库 | 01_Container
料箱、料架 |  |  |  |
|  |  |  | 02_Human_Models
人体模型 |  |  |  |
|  |  |  | 03_Controller
控制柜（机器人，PDP wdp） |  |  |  |
|  |  |  | 04_CablePackage
线缆、桥架 |  |  |  |
|  |  |  | 05_TurnTable
转台 |  |  |  |
|  |  |  | 06_Electric
其他电气设备 |  |  |  |
|  |  |  | 07_Equipment
连接设备控制柜 | 01_Arc_welding
弧焊设备 | （品牌） |  |
|  |  |  |  | 02_PressMechine
压机、机床 | （品牌） |  |
|  |  |  |  | 03_Sealing
涂胶设备 | （品牌） |  |
|  |  |  |  | 04_Stud_Welding
螺柱焊机 | （品牌） |  |
|  |  |  |  | 05_Nut_Welding
凸焊设备 | （品牌） |  |
|  |  |  |  | 06_Vision
视觉系统（拍照、检测） | （品牌） |  |
|  |  |  |  | 07_SPR
SPR设备 | （品牌） |  |
|  |  |  |  | 08_FDS
FDS设备 | （品牌） |  |
|  |  |  |  | 09_TighteningDevice
拧紧设备 | （品牌） |  |
|  |  |  |  | 10_Laser
激光焊设备 | （品牌） |  |
|  |  |  |  | 11_Spot welding
点焊设备 | （品牌） |  |
|  |  |  | 08_Gun
机器人工具 | 01_ArcGun
弧焊枪 | （品牌） |  |
|  |  |  |  | 02_Hemming
滚边头 | （品牌） |  |
|  |  |  |  | 03_LaserGun
激光焊枪 | （品牌） |  |
|  |  |  |  | 04_StudGun
螺柱焊枪 | （品牌） |  |
|  |  |  |  | 05_SealingGun
涂胶枪 | （品牌） |  |
|  |  |  |  | 06_SprotGun
点焊枪 | （品牌） | X_Gun |
|  |  |  |  |  |  | C_Gun |
|  |  |  |  | 07_Sleeve
套筒 | （品牌） |  |
|  |  |  |  | 08_Tighten
拧紧轴 | （品牌） |  |
|  |  |  |  | 09_FDS_Gun
钻铆枪 | （品牌） |  |
|  |  |  |  | 10_SPR_Gun
铆接枪 | （品牌） |  |
|  |  |  |  | 11_Arplus
阿普拉斯焊枪 | （品牌） |  |
|  |  |  | 09_Robots
机器人 | KUKA
（品牌） |  |  |
|  |  |  |  | ABB
（品牌） |  |  |
|  |  |  |  | FANUC
（品牌） |  |  |
|  |  |  | 10_Safety
安全设备 | 01_Exhaust
排风、除尘设备 |  |  |
|  |  |  |  | 02_Fences
围栏 |  |  |
|  |  |  |  | 03_RollerDoor
卷帘门 |  |  |
|  |  |  |  | 04_Scanners
扫描设备（光栅、雷达） |  |  |
|  |  |  | 11_Robot_Equipment
机器人附属设备 | 01_ToolChanger
换枪盘、过渡板 | （品牌） |  |
|  |  |  |  | 02_7axis
7轴 | （品牌） |  |
|  |  |  |  | 03_RobotBase
机器人底座 |  |  |
|  |  |  |  | 04_RobCable
机器人管线包 | （品牌） |  |
|  |  |  |  | 05_Tip_Dress
修模器 | （品牌） |  |
|  |  |  |  | 06_TCP_Stand
TCP检测支架 |  |  |
|  |  |  |  | 07_Hip_Rip
水汽排 |  |  |
|  |  | Standard_Library-XXX
标准资源库 |     供应商标准资源库，XXX为供应商公司名称缩写，细分文件夹结构层级同上，用于存放因项目需求供应商添加的标准资源。
    尽量统一使用上面的标准资源，只有上面资源有缺失、不满足项目要求或存在问题时才在此处添加资源。上面标准资源只允许调用，不允许编辑、修改、删减，更不允许Modeling修改结构。 |  |  |  |
|  |  | Project_Library
项目非标资源库 | XXX
工厂代号（例如：BJ01) | XXX
项目代号（例如：MS11) | XX
线体（例如：UB） | 01_Fixture
夹具 |
|  |  |  |  |  |  | 02_Gripper
抓具 |
|  |  |  |  |  |  | 03_GripperStand
抓具支架 |
|  |  |  |  |  |  | 04_GunStand
焊枪支架 |
|  |  |  |  |  |  | 05_Trolley
手推车 |
|  |  |  |  |  |  | 06_Conveyor
输送设备 |
|  |  |  |  |  |  | 07_Buffers
缓村设备、存储设备 |
|  |  |  |  |  |  | 08_Worktable
工作台，置台 |
|  |  |  |  |  |  | 09_Platform
平台、踏台 |
|  |  |  |  |  |  | 10_Steel_Structrue
钢结构 |
|  |  |  |  |  |  | 11_Layout
存放2D布局图 |
|  |  | Product
车身数据 | XXX
车型代号（例如：MS11) | Underbody
下车体 | FE
机舱 |  |
|  |  |  |  |  | FF
前地板 |  |
|  |  |  |  |  | RF
后地板 |  |
|  |  |  |  | Upperbody
上车体 | BS
侧围 |  |
|  |  |  |  |  | Roof
顶盖 |  |
|  |  |  |  | Closure
闭合件 | HD
前盖 |  |
|  |  |  |  |  | FD
前门 |  |
|  |  |  |  |  | RD
后门 |  |
|  |  |  |  |  | TG
尾门 |  |
|  |  | Mfg
焊点数据 | XXX
车型代号（例如：MS11) |  |  |  |
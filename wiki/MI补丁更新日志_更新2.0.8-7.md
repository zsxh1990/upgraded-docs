# MI补丁更新日志_更新2.0.8-7

序号 | 大版本 | 补丁版本 | 发布日期 | 更新内容 | 更新文件 | 安装方式 | 参考图例1 | 参考图例2 | 参考图例3
1 | 2.0.8 | 2.0.8-1 | 2026-04-04 00:00:00 | MI02：修复当选Stabli换枪盘时，运行ChangeTool指令发生读取信号状态失败的问题 | mi02_tch_rout.pc | Load
2 | 2.0.8 | 2.0.8-2 | 2026-04-07 00:00:00 | MI08：Glue_End指令逻辑更改
原逻辑：①检测到上下限信号非ON时，需等待信号正常，DO[893]脉冲300ms；②检测到General Fault为OFF时，发R[28]=1；
更改后：①检测到上下限信号非ON时，发R[28]=1，不再等待信号正常；②检测上下限信号（任意状态）后，发DO[893]脉冲1s；③不再检测General Fault | mi08_dsp_rout.pc | Load
3 | 2.0.8 | 2.0.8-3 | 2026-04-10 00:00:00 | MI01：自动备份功能逻辑优化，设置的内容不覆盖机器人端口1的IP | mi01_cmn_gui.pc | Load
4 | 2026-04-10 00:00:00 | MI01：新增ProgNumCheck指令，用于检测程序号，调用格式为 CALL MI01_CMN(ProgNumCheck, ProgNo.=10, '...') | ARGDISPEG01.DT
ARGDISPCH01.DT
mi01_cmn.pc
mi01_cmn_rout.pc | DT文件copy至FR根目录，重启生效；pc直接load
5 | 2026-04-10 00:00:00 | MI08：在2.0.8-2基础上对Glue_End指令逻辑进行更改
GUI选择SCA品牌时，Glue_End不再对application OK信号做处理；
GUI选择DMTS品牌时，维持不变； | mi08_dsp_rout.pc | Load
6 | 2.0.8 | 2.0.8-4 | 2026-04-16 00:00:00 | MI09：使用Coherix 3D, Control Run Mode, ON指令时，逻辑增加对Automatic Mode的DI信号检测 | mi09_dsd_rout.pc | Load
7 | 2.0.8 | 2.0.8-5 | 2026-04-18 00:00:00 | MI08：对于新SCA设备的车身码传输，新增后台映射功能，调用方法改变：
CALL MI08_DSP(SendBSNNum, GunNo.=1, SCA New, '...')，第三个参数选择SCA New时使用新的规则，选择SCA Old时使用老规则。
更新后注意：TP程序中，SendBSNNum该行指令需要修改完成刷新，可删除后重新插入，也可选择任意其他指令（如Glue_Init）再选回SendBSNNum。
加载mi08_dsp_mon.pc文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi08_dsp_mon程序；
2）选中MI08_DSP_VAR，找到Karel Data-b_mon_08并改成FALSE，再加载mi08_dsp_mon程序。 | ARGDISPEG08.DT
ARGDISPCH08.DT
mi08_dsp.pc
mi08_dsp_rout.pc
mi08_dsp_mon.pc | DT文件copy至FR根目录，重启生效；pc直接load
8 | MI03：后台逻辑增加VACCUM配置的DI信号向PLC发送DO信号的映射功能。
加载mi03_grp_mon.pc文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi03_grp_mon程序；
2）选中MI03_GRP_VAR，找到Karel Data-b_mon_03并改成FALSE，再加载mi03_grp_mon程序。 | mi03_grp_mon.pc | Load
9 | 2.0.8 | 2.0.8-6 | 2026-04-20 00:00:00 | MI03：后台逻辑优化IO映射，修复变量未初始化问题。
加载mi03_grp_mon.pc文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi03_grp_mon程序；
2）选中MI03_GRP_VAR，找到Karel Data-b_mon_03并改成FALSE，再加载mi03_grp_mon程序。 | mi03_grp.pc
mi03_grp_mon.pc | Load
10 | 2.0.8 | 2.0.8-7 | 2026-04-28 00:00:00 | MI03：后台逻辑优化IO映射，增加cylinder close信号的映射。
加载mi03_grp_mon.pc文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi03_grp_mon程序；
2）选中MI03_GRP_VAR，找到Karel Data-b_mon_03并改成FALSE，再加载mi03_grp_mon程序。 | mi03_grp_mon.pc | Load
11 | MI08：在2.0.8-5基础上更新，删除BSN Enable（DO[172]）的脉冲逻辑。 | mi08_dsp.pc | Load
序号 | 大版本 | 补丁版本 | 发布日期 | 更新内容 | 更新文件 | 安装方式 | 参考图例1 | 参考图例2 | 参考图例3
1 | 2.0.7 | 2.0.7-1 | 2025-12-18 00:00:00 | FRES - RobotHeld - remove指令，删除对DI_MOTOR_RUNNING的检测 | mi04_ssw_rout.pc | Load
2 | 2.0.7 | 2.0.7-2 | 2025-12-18 00:00:00 | 针对MI20和MI21，在MI SETUP界面中选双枪并按F5执行，对图中的“焊接设备数量”和“焊接控制器数量”自动设为2（反之单枪设为1） | mi20_swh_gui.pc
mi21_swh_gui.pc | Load
3 | 2.0.7 | 无 | 2025-12-25 00:00:00 | MI14 Rivet ID从999999扩展到9999999 | ARGDISPEG14.DT
ARGDISPCH14.DT | Copy至FR根目录，重启生效
4 | 2.0.7 | 2.0.7-3 | 2025-12-31 00:00:00 | MI04 GUI中选Bosch并按F5执行后，weld sched ack处配置改为DI[2648] | mi04_ssw_gui.pc | Load
5 | 2.0.7 | 2.0.7-4 | 2026-01-13 00:00:00 | DCS 速度位置检查功能自动启用 | dcsbook.xvr | Load
修磨器BK模块分配IO需按模块品牌自动配置，选1不做分配，选2按照Eclo分配，选3按照Balluff分配 | apps_loader.pc | Load
定位抓手切换在激活第二个安士能模块时，检测图中信号为ON时表示正常 | mi02_tch_mon.pc | Load，如提示运行中请先终止
DCS工具球停放站信号自动关联 | 02atcheg.tx
atch.vr
mi02_tch_gui.pc | tx文件cory至FR根目录；
vr和pc直接load
抓手应用激活抓手号，参数未初始化问题解决 | mi03_grp.pc | Load
Bosch水冷焊机自动断水功能（SMC）开发，通过DI[48]上升沿触发；断水后先输出关水DO，再检测水流量为OFF；如水流量未OFF，再次触发DI[48]无效 | mi04_ssw_mon.pc | Load，如提示运行中请先终止
空运行模式不检测水流量（SMC）
B2部分工位无法在点焊时输出进程中问题解决，检测Error信号为ON时表示正常，输出DO[42]
涂胶电压标定模拟量上限锁死2000问题，修改变量扩大到65535 | dspbook.xvr | Load
mi08 purge功能修改：根据GUI中的品牌选择，SCA发Purge信号为长ON，大铭保持不变（发脉冲信号） | mi08_dsp_rout.pc | Load
6 | 2.0.7 | 2.0.7-5 | 2026-01-16 00:00:00 | 针对Arplas焊修磨功能，创建修磨设置界面、及相关程序指令；GI接口使用GI[112]，R[32]显示本次修磨量，R[33]显示累计修磨量，TCP使用1号 | 11aapleg.tx
aapl.vr
mi11_apl_gui.pc
mi11_apl.pc
mi11_apl_rout.pc
ARGDISPEG11.DT
ARGDISPCH11.DT | DT文件copy至FR根目录，重启生效；tx文件cory至FR根目录；
vr和pc直接load
7 | 2.0.7 | 2.0.7-6 | 2026-01-17 00:00:00 | 优化Arplas修磨逻辑，读取GI[112]做当前修磨量值，不累加 | mi11_apl_rout.pc | Load
8 | 2.0.7 | 2.0.7-7 | 2026-01-23 00:00:00 | MI08 End指令，DO[CYCLE END]置OFF后，加延迟300ms，再给GO复位 | mi08_dsp_rout.pc | Load
9 | 2.0.7 | 2.0.7-8 | 2026-01-28 00:00:00 | TP程序监控功能，在界面中进行TP程序选择的配置，对修改记录进行监控，并上传PLC。安装后，通过菜单MENU-STATUS-PROG RECORD进入界面进行配置。 | mi01_rec_gui.pc
mi01_rec_mon.pc
mi01_rec_var.pc
rcproceg.tx
proc.vr | 执行cm文件安装
10 | 2.0.7 | 2.0.7-9 | 2026-01-29 00:00:00 | Glue Init选T_Dsp On时，不再对DI[929]进行检测 | mi08_dsp_rout.pc | Load
11 | 2.0.7 | 2.0.7-10 | 2026-02-06 00:00:00 | MI08 SETUP界面中新增选项：
a) 当胶机品牌选择SCA时，需要继续选择当前胶机是单组份胶机或者双组份胶机，在TP指令中选择T_DSP OFF或者ON进行单组份排胶或者双组份排胶：
选单组份胶机时，指令选T_DSP OFF或者ON，排胶逻辑和以前一致(DO[889])；
选双组份胶机时，指令选T_DSP OFF表示单组份排胶(DO[921])，指令选T_DSP ON表示双组份排胶(DO[889])；
b) 当胶机品牌选DMTS时，指令选T_DSP OFF或者ON，排胶逻辑和以前一致(DO[889])
原执行start指令时输出DO[42]和End指令时DO[42]复位，仅在非空循环模式；现更改未不区分是否在空循环模式。 | 08adspeg.tx
adsp.vr
mi08_dsp.pc
mi08_dsp_rout.pc
mi08_dsp_gui.pc
mi08_dsp_var.pc
ARGDISPEG08.DT
ARGDISPCH08.DT | DT文件copy至FR根目录，重启生效；tx文件cory至FR根目录；
vr和pc直接load
12 | 2.0.7 | 2.0.7-11 | 2026-03-10 00:00:00 | 以下应用及指令增加DO[42]的输出：
MI08：Purge指令（需先安装2.0.7-10其他文件）
MI11：Arplasweld和Dimple指令（需先安装2.0.7-5其他文件）
MI19：Start和End指令
MI20：Start指令
MI21：Start指令 | mi08_dsp.pc
mi11_apl.pc
mi19_cli.pc
mi20_swh.pc
mi21_swh.pc | Load
GO[6] Error Code输出优化：
MI04：删除GO[6]=39、41的逻辑（需先安装2.0.7-10其他文件）
MI08：删除GO[6]=53、63、73的逻辑 | mi04_ssw_rout.pc
mi08_dsp_rout.pc | Load
MI01：自动模式下按Status键不再产生Servo OFF报警
更新注意事项：
MI01_cmn_mon程序一直处在运行中，需选中MI01_cmn_var程序并更改Karel变量b_mon_01为False，再加载更新后的文件；重启机器人或者执行任意MI01指令可重新使MIi01_cmn_mon运行；
其他文件为界面相关设置，可选更新（如不更新也不影响使用） | 01acmneg.tx
acmn.vr
mi01_cmn_gui.pc
mi01_cmn_mon.pc | tx文件cory至FR根目录；
vr和pc直接load
13 | 2.0.7 | 2.0.7-12 | 2026-03-12 00:00:00 | MI02：针对瓦尔特换枪盘，无法抓取Tool 2的问题，进行逻辑优化 | mi02_tch_rout.pc | Load
MI14：Dock逻辑中，先发DO1139，再等待DI1139 | mi14_spr_rout.pc | Load
MI09：更改Coherix第2个设备的IO地址偏移量为3512；新机导入时有效，已有程序时需手动更改变量地址 | mi09_dsd.pc | Load
MI08：End指令先判断上下限报警，如果没有，发DO[893]复位，如果再有综合报警，R28=1 | mi08_dsp_rout.pc | Load
MI08：加入Leoni TCP的TP程序模板和PC程序（无MI包版本号） | tcp_comm.pc
tcp_mea.pc
tcp_msg.pc
PNS0140.TP
TCP_CHECK_X.TP
TCP_3DCAL_GUN_X.TP
TCP_3DCOM_GUN_X.TP | Load
app setup：
MI08 IO分配：弹窗选择是否有Leoni TCP，如有则进行IO自动分配
MI09 IO分配：更新Coherix 3D第2个设备的IO分配，并删除第3个设备的分配
MI标准软件包安装和IO分配按键，完成后增加弹窗确认 | apps_loader.pc | Load
14 | 2.0.7 | 2.0.7-13 | 2026-03-18 00:00:00 | MI01自动备份页面：
MI GUI-MI01 Setup-AutoBack自动备份页面，Robot IP从输入方式改为自动读取Host Comm-TCP/TP页面中端口2的IP地址；其他设置方法不变 | mi01_cmn_gui.pc
01acmneg.tx
acmn.vr | tx文件cory至FR根目录；
vr和pc直接load
15 | 2.0.7 | 2.0.7-14 | 2026-03-20 00:00:00 | IO自动分配功能修改：MI08选DMTS时对应的IO长度从80个更改为160个 | apps_loader.pc | Load
16 | 2.0.7 | 2.0.7-15 | 2026-03-20 00:00:00 | MI08后台更新：删除DI[UPER_LIMIT]、DI[LOWER_LIMIT] 与DO[445]的关联
加载文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi08_dsp_mon程序；
2）选中MI08_DSP_VAR，找到Karel Data-b_mon_08并改成FALSE，再加载MI08_dsp_mon程序 | mi08_dsp_mon.pc | Load
17 | 2.0.7 | 2.0.7-16 | 2026-03-24 00:00:00 | MI02：针对瓦尔特换枪盘，DI[2702]为ON时代表当前工具号为4，更新ChangeTool和CheckToolCode功能模块 | mi02_tch_rout.pc | Load
18 | MI03后台更新：后台IO映射给PLC的DO信号区间保持不变，无论抓手号是多少
加载文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi03_dsp_mon程序；
2）选中MI03_GRP_VAR，找到Karel Data-b_mon_03并改成FALSE，再加载MI03_grp_mon程序 | mi03_grp_mon.pc | Load
19 | MI04后台更新：针对Bosch焊机DO[42]在进程中的后台输出，增加DO[515] = OFF作为关闭DO[42]的一个条件
加载文件如提示运行中，请按以下任意一种方式操作：
1）重启后立刻加载mi04_ssw_mon程序；
2）选中MI04_SSW_VAR，找到Karel Data-b_mon_04并改成FALSE，再加载MI04_ssw_mon程序 | mi04_ssw_mon.pc | Load
20 | 2.0.7 | 2.0.7-17 | 2026-03-31 00:00:00 | MI01自动备份页面：
MI GUI-MI01 Setup-AutoBack自动备份页面，增加一个Server服务器，形成两个不同的FTP端口，用于自动备份；使用Enable和Disable来启用/禁用该端口；两个端口都启用的情况下，备份时先备份Server 1，再备份Server2 | mi01_autobk.pc
mi01_cmn_gui.pc
01acmneg.tx
acmn.vr | tx文件cory至FR根目录；
vr和pc直接load
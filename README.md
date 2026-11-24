<div align="center">
  <h1 align="center">
    <img src="icons/icon.png" width="160" alt="coco-ww logo"/>
    <br/>
    coco-ww
  </h1>

  <p>
    基于 <a href="https://github.com/ok-oldking/ok-wuthering-waves">ok-ww</a> v3.8.0 裁剪、自己维护的鸣潮自动战斗程序，只做「自动战斗」和「刷 4C 声骸」两件事。
    <br />
    框架是 <a href="https://ok-script.com">ok-script</a>。
  </p>

  <p><i>通过 Windows 接口模拟用户操作，无内存读取、无文件修改</i></p>
</div>

<div align="center">

![平台](https://img.shields.io/badge/platform-Windows-blue)
![协议](https://img.shields.io/badge/license-AGPL--3.0-green)
![上游](https://img.shields.io/badge/基于-ok--ww%20v3.8.0-orange)

</div>

<p align="center">
  <strong>队伍代码工坊：</strong><a href="https://github.com/wk321425/coco-ww-char-code">wk321425/coco-ww-char-code</a>
  &nbsp;·&nbsp;
  <strong>做轴手册（AI skill）：</strong><a href="https://github.com/wk321425/okww-axis">wk321425/okww-axis</a>
</p>

---

## ⚠️ 免责声明

本软件是外部辅助工具，用来自动化《鸣潮》的部分游戏流程。它只通过模拟常规的键鼠操作和游戏交互，不会修改任何游戏文件或数据。

本软件开源、免费，仅供个人学习与交流使用，请勿用于任何商业或营利性目的。**如果你是花钱买到的，请立即退款。** 因使用本软件产生的任何问题，均与本项目及开发者无关。

请注意，根据库洛官方的《鸣潮》公平运营声明：
> 严禁利用任何第三方工具破坏游戏体验。
> 我们将严厉打击使用外挂、加速器、作弊软件、宏脚本等违规工具的行为，这些行为包括但不限于自动挂机、技能加速、无敌模式、瞬移、修改游戏数据等操作。
> 一经查证，我们将视违规情况和次数，采取包括但不限于扣除违规收益、冻结或永久封禁游戏账号等措施。

**使用本软件即表示你已阅读、理解并同意以上声明，并自愿承担一切潜在风险（包括封号）。**

## 🥥 coco-ww 的特别之处

和上游 ok-ww 相比，coco-ww 把功能收窄到战斗，力气都花在「打得像样、绝不卡住」上。

### 1. 只留两个功能，不跟随上游更新

* 任务页只有 **自动战斗**（触发型）和 **刷 4C 声骸**（手动），日常、多账号等其他任务全部删掉。
* 断开了上游的自动更新，所有改动直接写在 `src/` 里，不会被更新覆盖，也不用再靠插件打补丁。
* 自己的更新走本仓库的 Release：「关于 → 应用更新」里直接点更新，只下载变了的文件（通常几百 KB），程序自动重启。`configs/` 里的设置和队伍代码不会被覆盖。

### 2. 按 F 开战，直接并进自动战斗

* 不用再装插件。自动战斗卡片里就有「按 F 开始战斗 / F 之后等待（秒） / 没锁到目标最多撑（秒）」三项设置。
* 走到怪面前按 F，程序接手开打，适合矩阵、深塔这类需要自己先站好位的场景。

### 3. 队伍代码与工坊

* 内置 **29 支队伍的自定义战斗轴**（`configs/custom_teams/`）。启动轴、循环轴照着攻略视频和鼠标宏逐键复刻，2K 和 1080p 都能跑。
* 「角色代码」页签连着在线工坊 [coco-ww-char-code](https://github.com/wk321425/coco-ww-char-code)，可以只看当前队伍，也可以一次拉下同一支队伍的所有代码。
* **同一支队伍可以存多份代码（存档）**，下拉一选就换。导入新代码时，旧的自动收进存档，不会被覆盖。
* 队伍列表显示游戏内头像，三个角色按 主C → 副C → 辅助 排序。

### 4. 跑轴记录与复盘

* 「跑轴记录」任务把每一局的切人、按键、读屏结果按时间存到 `logs/axis_runs/`。
* 复盘页签按时间线看每一圈、每一次切人。排查「慢了 / 大招没放出来 / 切早了 / 卡住」时有据可查，不用再靠猜；还能导出反馈包，发给做轴的人。

### 5. 原生角色代码加固

没有自定义代码的队伍走内置的原生角色代码。coco-ww 对照攻略视频和官方动作帧数据，逐个角色重写了这一部分：

* **任何状态都能跑**：变身/未变身、强化/未强化、大世界或重置过的副本、被 BOSS 和小怪打断，都不能卡住。单次上场有 30 秒硬上限（个别角色 40 秒），到点会松开所有按住的键并强制切人。
* **组队不打架**：切人加了防饿死机制，离场太久的角色会逐级抬高优先级，不会出现「一直不切某个人」。
* **按机制操作**：例如洛瑟菈长按 E 按到聚焦环跑满（解决「拍照拍不好」），仇远按住打满三段强化重击，卜灵重击前先读卦象、排错了自动补齐，夏空大招演唱会连点按钮。
* **标准配队按基础轴排**：爱达千、爱琳莫、绯洛穗、绯洛千、清达莫等按攻略的基础轴安排切人顺序；自由搭配的队伍只做每个角色的基础操作，不纠结极限。
* 每个角色的机制要点和改动理由都写在 [docs/角色操作笔记.md](docs/角色操作笔记.md)。
* 压测工具 `tools/char_fuzz.py` 用假游戏和模拟时钟，批量跑 36 支标准队和全部原生角色，专门抓卡死、按键没松开、某个角色长期上不了场这三类问题。

> 2026 年 11 月这批原生角色改动还没有逐个实机验证。遇到问题欢迎提 Issue，最好附上跑轴记录导出的反馈包。

## ✨ 沿用 ok-ww 的能力

* **分辨率**：支持 16:9 分辨率，最低 1280×720。
* **全角色自动识别**：换队后自动认出三个角色，不用手动配置技能序列。
* **截图与操作**：WGC 截图、PostMessage 发送按键，和上游一致。

## 🚀 运行

### 下载发布包（推荐）

1. 到 [Releases](https://github.com/wk321425/coco-ww/releases) 下载最新的 `coco-ww-vX.Y.Z.zip`（自带 Python 3.12 运行环境，不用另装）。
2. 解压到**纯英文路径**，例如 `D:\coco-ww`（框架硬性要求，路径里有中文会启动失败）。
3. 双击 `启动 coco-ww.bat`。它会自动请求管理员权限：鸣潮以管理员身份运行，coco-ww 也必须是，否则发不出按键。

以后有新版本，启动时会弹提示，点「更新」就行。版本列表、更新清单和文件都会轮流试 GitHub 和 jsDelivr 镜像，国内一般不用挂代理；v1.0.6 及更早的版本还是直连 GitHub 取版本列表，连不上就挂一次代理先更新到新版，之后就不用了。三种情况得重新下整包：v1.0.1 及更早的版本还没有在线更新功能；v1.0.6 及更早的版本点「更新」报证书错误（`CERTIFICATE_VERIFY_FAILED`）时，挂代理也没用，下 v1.0.7 或更新的整包；新版本换了运行环境（`requirements.txt` 变了）时，更新卡片会提示并给出下载按钮。重新下整包时把旧版的 `configs` 文件夹整个拷过来，就能保留设置和自己的队伍代码。

### 从源码运行

1. 把仓库克隆到**纯英文路径**下。
2. 安装 Python 3.12（[python.org](https://www.python.org/downloads/) 的安装版，带 `py` 启动器），然后建虚拟环境、装依赖：

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

3. 鸣潮以管理员身份运行，所以 coco-ww 也必须从**管理员终端**启动，否则发不出按键：

   ```powershell
   .\.venv\Scripts\python.exe -X utf8 main.py
   ```

从源码运行时 `启动 coco-ww.bat` 用不了：它用的是根目录 `python\` 里的运行环境，这个目录只在发布包里有，不进版本库。

## 🔧 疑难解答

提问前请先逐条排查：

1. **管理员身份**：游戏是管理员身份运行的，coco-ww 也必须是，否则按键和 F 开战都没反应。
2. **杀毒软件**：把安装目录加进杀毒软件（包括 Windows Defender）的信任区或白名单。
3. **显示设置**：关闭 Auto HDR（自动 HDR），关闭 RTX HDR、RTX 动态亮丽、AMD 锐化等显卡滤镜，使用游戏默认亮度，关闭帧率显示之类的画面叠加层。
4. **自定义按键**：改过游戏内默认按键的话，要在 coco-ww 设置里同步。
5. **游戏性能**：保证游戏稳定 60 FPS；不稳就降画质或分辨率。
6. **关闭自动奔跑**：在游戏设置里关掉。
7. **装备主声骸**：队伍里每个角色都必须装备主声骸（右下角有声骸技能图标），否则自动战斗会反复锁敌而不出手。
8. **换队后认错角色**：换队后识别出错时，重启 coco-ww 再进战斗。
9. **反馈问题**：附上跑轴记录导出的反馈包，或 `logs/ok-script.log`。

## 💻 开发者

| 目录 | 内容 |
| --- | --- |
| `src/char/` | 原生角色代码（每个角色一个文件，`BaseChar.py` 是公共基类） |
| `src/task/` | 自动战斗、刷 4C 声骸和切人逻辑（`BaseCombatTask.py`） |
| `src/combat/` | 按 F 开战、战斗状态判断、持宏者代码用的监视线程 |
| `src/recorder/` | 跑轴记录和复盘页签 |
| `src/gui/` | 角色代码 / 工坊页签 |
| `configs/custom_teams/` | 内置的自定义队伍代码 |
| `docs/` | 角色操作笔记 |
| `tools/` | 压测、图标等工具脚本 |

压测原生角色代码：

```powershell
# 不带参数：36 支标准队 + 每个原生角色配普通队友，各跑 3 局
.\.venv\Scripts\python.exe -X utf8 tools\char_fuzz.py
# 指定队伍，逐次打印上场和切人
.\.venv\Scripts\python.exe -X utf8 tools\char_fuzz.py --team Hiyuki,Lucilla,Chisa --seed 0 --trace
```

自己做战斗轴，可以配合做轴手册 [okww-axis](https://github.com/wk321425/okww-axis)（给编码代理用的 skill，记录了几十条轴实机踩出来的经验）。

## 📜 协议与致谢

* coco-ww 派生自 [ok-oldking/ok-wuthering-waves](https://github.com/ok-oldking/ok-wuthering-waves)，按同一协议 **AGPL-3.0** 开源，见 [LICENSE.txt](LICENSE.txt)。
* `ok/` 目录是 [ok-script](https://github.com/ok-oldking/ok-script) 框架（[ok-script.com](https://ok-script.com/)），使用 Apache-2.0 + Commons Clause 及附加条款，见 [ok/LICENSE.txt](ok/LICENSE.txt)。
* 识图模板、角色头像等游戏素材的版权归库洛游戏所有。
* 队伍代码的作者署名见各队伍目录下的 `team.json`。
* 角色机制参考了 B 站「打游戏的老二」的角色详细攻略系列视频和 [ww.nanoka.cc](https://ww.nanoka.cc) 的技能资料。
* 上游项目致谢的开源项目：[lazydog28/mc_auto_boss](https://github.com/lazydog28/mc_auto_boss)、[ok-oldking/OnnxOCR](https://github.com/ok-oldking/OnnxOCR)、[zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)、[Toufool/AutoSplit](https://github.com/Toufool/AutoSplit)。

维护：楽山（[wk321425](https://github.com/wk321425)）

# Ext Mic

这个目录提供打印机侧的最小独立采集服务。它运行在瑞芯微单板计算机上，手机只上传原始单声道 PCM；服务保存 session、音频、phase 和 manifest。服务不进入 Klipper 实时线程，也不接受任意 G-code。

## 部署

目标路径：

```text
代码和配置：/home/lava/printer_data/config/extended/ext-mic
录音目录：  /userdata/ext-mic/runs
HTTPS：     0.0.0.0:9443
```

启动（Python 3.11，只有标准库）：

```sh
/usr/bin/python3 /usr/local/bin/ext-mic.py --host 0.0.0.0 --port 9443 \
  --captures /userdata/ext-mic/runs \
  --certfile /home/lava/printer_data/config/extended/ext-mic/server.crt \
  --keyfile /home/lava/printer_data/config/extended/ext-mic/server.key
```

`init.d/S99ext-mic` 是 BusyBox init 脚本模板。它只负责启动 root runner、启停和状态检查；runner 等待 `/userdata` 和局域网地址就绪，创建录音目录、修正 `lava` 权限并准备证书，然后以 `lava` 用户启动独立的 Python listener。Python 子进程退出后 runner 会自动重启。PID 文件是 `/run/ext-mic-supervisor.pid`，子进程 PID 文件是 `/run/ext-mic-child.pid`；停止时会等待并终止残留 child，避免旧进程继续占用 9443。日志和录音仍保存在 `/userdata/ext-mic/`。`generate-cert.sh [目录] [主机名] [IP]` 以临时双文件生成证书，断电留下半成品时会清理后重建。传入打印机局域网 IP 后，手机可以直接访问局域网 HTTPS。首次使用时，iPhone 需要接受或安装这张证书。

Firmware Config 的 Services 页面提供 `Start Ext Mic`、`Stop Ext Mic` 和 `Restart Ext Mic` 三个固定入口。入口只调用 `/etc/init.d/S99ext-mic`，不携带采样参数，也不创建手机 session；session、phase 和实验动作仍由本服务 API 与打印机控制器负责。

常驻服务固定使用 TLS 监听局域网 `0.0.0.0:9443`。本地开发需要直接运行 `ext-mic.py --insecure-http`，不会改变固件服务入口。

在线把文件覆盖到运行中的 `/etc/init.d` 只适合立即验证；BusyBox `rcS` 会在 upper overlay 挂载前展开 `S??*` 文件列表，新增的 S99 不会自动进入下一次已经开始的启动流程。要验证整机重启自动启动，必须把这个目录作为固件 overlay 重打进 squashfs，再刷入设备。

## 手机页面

打开 `https://<printer-ip>:9443/ext-mic`。首次打开时，点一次“开始采集”并允许麦克风权限；成功采集过一次后，页面刷新会自动恢复。页面请求并显示实际采样率和打印机收到的速率：

- `channelCount=1`
- `sampleRate=48000`
- `echoCancellation=false`
- `noiseSuppression=false`
- `autoGainControl=false`

页面保留“开始采集”和“停止”两个控制。开始时会请求全屏（浏览器允许时），创建一次长寿命的手机传输 session，使用 `AudioWorklet` 将约 250 ms 的浮点样本转成 `s16le`，按序号上传。WebRTC 没有进入测量链；页面也不会发任意 G-code。页面每 5 秒检查服务状态和页面版本；页面版本变化时刷新，服务重启由当前 session 状态驱动重新连接。打印机控制器结束一个实验 run 时只写 `IDLE_AFTER` 和 run metadata，不停止手机传输 session。断链、服务重启或 session 被外部结束时，页面会释放旧音频节点并自动建立新的 session。手机切到后台时页面可能被系统暂停，回到前台后会恢复或重新开始。单块上传失败后页面断开采集节点，不会继续制造 gap。

## HTTP API

健康检查（无需 token）：

```http
GET /healthz
```

`GET /v1/status` 和 `/healthz` 返回 `instance_id` 与 `phone_revision`。手机页用这两个字段发现服务重启或页面替换，并完成一次受控刷新。

创建 session：

```http
POST /v1/mic/session
Content-Type: application/json

{}
```

响应只返回一次性 token。所有后续接口只接受 `Authorization: Bearer <token>`，不接受 query、表单或自定义 token 头。服务把 SHA-256 token 摘要写入 `session.json`，不会保存明文 token。服务同时只允许一个 `OPEN` session；页面离开或传输失败时会请求结束它。新页面创建 session 时会先结束遗留的 `OPEN` session，保留旧录音和 manifest，再开始新的采集。服务把连续 10 分钟没有音频块、phase 或动作记录的 `OPEN` session 标记为 `EXPIRED`，并生成 manifest。这样手机断电、切后台或网络中断后，旧 session 不会永久占住入口；过期 session 的原始文件仍然保留，但不进入正式分析。

上传 PCM：

```http
POST /v1/mic/<id>/chunk
Authorization: Bearer <token>
Content-Type: application/octet-stream
X-Seq: 0
X-Sample-Count: 12000
X-Sample-Rate: 48000
X-Channels: 1

<little-endian signed 16-bit PCM bytes>
```

服务只接受 `44100` 或 `48000` Hz、单声道 PCM。它检查 `sample_count × channels × 2` 与请求体长度相等。采样率和通道数在首块确定，后续改变会拒绝。序号跳过会记录 gap；重复或迟到序号拒绝。每块写入后原子更新 `status.json`，突然断电不会留下半个 JSON 文件。

查询传输状态：

```http
GET  /v1/mic/<id>/status
```

打印机端写 phase：

```http
POST /v1/mic/<id>/phase
Authorization: Bearer <token>
Content-Type: application/json

{"phase_id":"CIRCLE_PROBE","metadata":{"speed_mm_s":80}}
```

`phase` 记录服务器单调时钟、PCM 字节偏移和样本偏移，因此分析器可以把轨迹状态和音频对齐。手机页面不自动写 phase。

命名动作接口：

```http
POST /v1/run
Authorization: Bearer <token>
Content-Type: application/json

{"session_id":"<id>","action":"circle_probe","params":{"radius_mm":60,"speed_mm_s":80}}
```

只接受 `idle`、`circle_probe`、`line_x`、`line_y`。服务只记录白名单请求并返回 `run_id`、`status=RECORDED_ONLY`；当前版本不连接 Moonraker，也不执行运动。真正的打印机控制器负责检查安全状态、发 G-code、写 phase 和在完成后恢复 stock。这一层保证手机永远没有任意 G-code 入口。printer adapter 使用 `X-Printer-Controller: local` 访问 `/v1/mic/active/status`、`/v1/mic/active/run` 和 `/v1/mic/active/phase`，只记录当前手机传输，不接管麦克风，也不创建或停止传输 session。

## 目录和质量字段

每个 session 位于 `--captures` 下：

```text
<session_id>/
  session.json     # 创建时的元数据和 token 摘要
  audio.raw        # 追加写入的 s16le PCM
  status.json      # 采集中的状态和质量
  chunks.jsonl     # 每个已接受 PCM 块的序号、偏移和接收时间
  phases.json      # 打印机时间 marker
  runs.json        # 白名单动作请求
  manifest.json    # 服务重启或传输超时后的完整清单
```

`status.json` 和响应中的质量字段包括 `status`（`WAITING`、`OK`、`LOW_CONFIDENCE` 或 `INVALID`）、`settings_incomplete`、累计 `rms_dbfs`、`clip_ratio`、`packet_loss`、采样率、通道数、块数、字节数、gap 数和 `gap_ranges`。`chunks.jsonl` 为 FFT 按块跳过缺口提供样本与字节偏移。浏览器实际 DSP 开关缺失、打开或实际采样率/声道不合要求时，会明确标 `settings_incomplete=true` 和 `LOW_CONFIDENCE`。它们是同机相对测量指标，不是未经声学校准的 `dBA`。

## 自检

```sh
python3 -m py_compile ext-mic.py
```

自检会启动本地服务，覆盖 healthz、session、错误 token、PCM 字节校验、非法采样率/声道、跳号、重复序号、phase、动作白名单、状态查询、manifest 和服务重启封存。它不会连接真实打印机，也不会移动平台。

## 后续接入边界

打印机侧控制器可以把本服务当作 `MicSource`：读取 active session、在轨迹阶段写 phase、记录 run metadata。一个 run 结束时只写 `IDLE_AFTER`，手机传输继续存在。打印机重启时，服务封存旧的传输 epoch，页面自动重新连接并建立新的 epoch；这属于服务恢复，不是用户开始或停止采集。当前 `/v1/run` 只把四种命名动作记录为 `status=RECORDED_ONLY`，没有连接 Moonraker，也不会让打印机运动；真正的 printer adapter 在另一进程负责安全检查、轨迹、phase 和回滚。以后固定 MEMS 节点只需替换 PCM provider；传输 epoch、phase 和分析器格式不变。


\n

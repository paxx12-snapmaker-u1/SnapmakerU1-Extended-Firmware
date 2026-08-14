#!/bin/bash

set -xeo pipefail

case "$1" in
  start-timelapse)
    json='{"jsonrpc": "2.0", "method": "camera.start_timelapse", "params": {"mode": "classic", "frame_rate": 24, "type": "new", "gcode_name": "OrcaCube_PETG_7m5s", "gcode_path": "/home/lava/printer_data/gcodes/OrcaCube_PETG_7m5s.gcode"}, "id": 9840}'
    # camera/response {"id":9840,"jsonrpc":"2.0","result":{"state":"success"}}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_status_change","params":[{"timelapse":true,"timestamp":"2025-11-23 14:10:35"}]}
    ;;
  stop-timelapse)
    json='{"jsonrpc": "2.0", "method": "camera.stop_timelapse", "params": {}, "id": 9841}'
    # camera/response {"id":9841,"jsonrpc":"2.0","result":{"state":"success"}}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_status_change","params":[{"timelapse":false,"timestamp":"2025-11-23 14:10:46"}]}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_status_change","params":[{"timelapse":false,"timestamp":"2025-11-23 14:14:07"}]}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_generate_video_progress","params":[{"percent":80,"state":"generating"}]}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_generate_video_completed"}
    ;;
  start-monitor)
    json='{"jsonrpc": "2.0", "method": "camera.start_monitor", "id": 3524996744930656478, "params": {"domain": "lan", "interval": 0}}'
    # camera/response {"id":3524996744930656478,"jsonrpc":"2.0","result":{"state":"success","url":"/files/camera/monitor.jpg"}}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_status_change","params":[{"monitor_domain":"lan","monitoring":true,"timestamp":"2025-11-23 14:09:49"}]}
    ;;
  stop-monitor)
    json='{"jsonrpc": "2.0", "method": "camera.stop_monitor", "id": 3524996744930656480, "params": {"domain": "lan"}}'
    # camera/response {"id":3524996744930656480,"jsonrpc":"2.0","result":{"state":"success"}}
    # camera/notification {"jsonrpc":"2.0","method":"notify_camera_status_change","params":[{"monitor_domain":"lan","monitoring":false,"timestamp":"2025-11-23 14:10:07"}]}
    ;;
  take-photo)
    json='{"jsonrpc": "2.0", "method": "camera.take_a_photo", "params": {"reason": "printing", "timestamp": false, "filepath": "/tmp/tmp.jpg"}, "id": 6488}'
    # camera/response {"id":6488,"jsonrpc":"2.0","result":{"state":"success"}}
    ;;
  get-status)
    json='{"jsonrpc": "2.0", "method": "camera.get_status", "params": {}, "id": 6488}'
    # camera/response {"id":6488,"jsonrpc":"2.0","result":{"interface_type":"MIPI","monitoring":false,"state":"success","timelapse":false}}
    ;;
  get-timelapse-instance)
    json='{"jsonrpc": "2.0", "method": "camera.get_timelapse_instance", "params": {"page_index": 1, "page_rows": 10}, "id": 8}'
    # camera/response {"id":8,"jsonrpc":"2.0","result":{"count":2,"instances":[{"date_index":"20251128114039","gcode_name":"OrcaCube_PETG_7m5s","gcode_path":"/home/lava/printer_data/gcodes/OrcaCube_PETG_7m5s.gcode","generate_date":"2025-11-28","thumbnail_path":"/userdata/.tmp_timelapse/20251128114039/thumbnail.jpg","timelapse_dir":"/userdata/.tmp_timelapse/20251128114039","video_duration":"00:00","video_path":"/userdata/.tmp_timelapse/20251128114039/timelapse_f6_r24_classic.mp4"}],"page_index":1,"page_rows":10,"state":"success","total_count":2}}
    ;;
  delete-timelapse-instance)
    json='{"jsonrpc": "2.0", "method": "camera.delete_timelapse_instance", "params": {"date_index": "20251128114804"}, "id": 10}'
    # camera/response {"id":10,"jsonrpc":"2.0","result":{"state":"success"}}
    ;;
  detect-bed)
    json='{"jsonrpc": "2.0", "method": "camera.detect_capture", "params": {"labels": ["item"], "item": {"threshold": 0.5, "sensitivity": "low", "ignore_ratio": 1.0}}, "id": 2}'
    # camera/response {"id":2,"jsonrpc":"2.0","result":{"err_code":0,"item":{"max_prob":0.0,"obj_areas":0.0,"obj_cnts":0,"obj_probs":0.0},"state":"success"}}
    ;;
  detect-residue)
    json='{"jsonrpc": "2.0", "method": "camera.detect_capture", "params": {"labels": ["noodle", "residue"], "noodle": {"threshold": 0.5, "sensitivity": "low"}, "residue": {"threshold": 0.5, "sensitivity": "low"}}, "id": 90}'
    # camera/response {"id":90,"jsonrpc":"2.0","result":{"err_code":0,"noodle":{"max_prob":0.0,"obj_areas":0.0,"obj_cnts":0,"obj_probs":0.0},"residue":{"max_prob":0.0,"obj_areas":0.0,"obj_cnts":0,"obj_probs":0.0},"state":"success"}}
    ;;
  simulate-timelapse)
    export ONLY_RESPONSE=1
    mosquitto_sub -v -h localhost -t camera/# &
    $0 start-timelapse
    for i in {1..5}; do
      sleep 1
      $0 take-photo
    done
    $0 stop-timelapse
    echo Done
    wait
    exit 0
    ;;

  *)
    echo "Usage: $0 {start-timelapse|stop-timelapse|start-monitor|stop-monitor|take-photo|get-status|get-timelapse-instance|delete-timelapse-instance|detect-bed|detect-residue|simulate-timelapse}"
    exit 1
    ;;
esac

# /userdata/.tmp_timelapse content during timelapse:
# During:
# ./20251123141359
# ./20251123141359/index_next
# ./20251123141359/3.jpg
# ./20251123141359/2.jpg
# ./20251123141359/config.json
# ./20251123141359/state
# ./20251123141359/1.jpg
# ./current
# ./.lock

# /userdata/.tmp_timelapse content after timelapse:
# ./20251123141359
# ./20251123141359/index_next
# ./20251123141359/gen_thumbnail.log
# ./20251123141359/3.jpg
# ./20251123141359/4.jpg
# ./20251123141359/2.jpg
# ./20251123141359/5.jpg
# ./20251123141359/progress.txt
# ./20251123141359/0.jpg
# ./20251123141359/config.json
# ./20251123141359/thumbnail.jpg
# ./20251123141359/check_video.log
# ./20251123141359/state
# ./20251123141359/gen_video.log
# ./20251123141359/1.jpg
# ./20251123141359/timelapse_f6_r24_classic.mp4
# ./timelapse.json

# The config.json
# {
#     "frame_rate": 24,
#     "gcode_name": "OrcaCube_PETG_7m5s",
#     "gcode_path": "/home/lava/printer_data/gcodes/OrcaCube_PETG_7m5s.gcode",
#     "mode": "classic"
# }

# The timelapse.json
# [ "count": 3, "instances": [
# {
#     "date_index": "20251120175742",
#     "gcode_name": "00c588d8-ee61-45d3-bd96-bc41d18fa4c1_PLA_1h10m",
#     "gcode_path": "/home/lava/printer_data/gcodes/Test/00c588d8-ee61-45d3-bd96-bc41d18fa4c1_PLA_1h10m.gcode",
#     "generate_date": "2025-11-20",
#     "thumbnail_path": "/userdata/.tmp_timelapse/20251120175742/thumbnail.jpg",
#     "timelapse_dir": "/userdata/.tmp_timelapse/20251120175742",
#     "video_duration": "00:13",
#     "video_path": "/userdata/.tmp_timelapse/20251120175742/timelapse_f313_r24_classic.mp4"
# } ] ]

if [[ -n "$ONLY_RESPONSE" ]]; then
  mosquitto_sub -v -h localhost -t camera/response -C 1 &
else
  mosquitto_sub -v -h localhost -t camera/# &
fi

mosquitto_pub -h localhost -t camera/request -m "$json"
wait

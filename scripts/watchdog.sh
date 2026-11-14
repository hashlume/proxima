#!/bin/bash
# Supervisor entry point: run start.sh now, then every minute, so a dead screen,
# a crashed seeker or a dropped tunnel comes back without anyone logged in.
while true; do
  /root/proxima/start.sh >> /root/proxima/watchdog.log 2>&1
  sleep 60
done

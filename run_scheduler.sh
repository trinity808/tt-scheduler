#!/bin/bash

cd "/Users/anand/Projects/trinity-dropbox-scheduler"

echo "========================================" >> logs/scheduler.log
echo "Scheduler started at: $(date '+%Y-%m-%d %H:%M:%S')" >> logs/scheduler.log
echo "========================================" >> logs/scheduler.log

source venv/bin/activate

python main.py >> logs/scheduler.log 2>> logs/scheduler_error.log

echo "Scheduler finished at: $(date '+%Y-%m-%d %H:%M:%S')" >> logs/scheduler.log
echo "" >> logs/scheduler.log
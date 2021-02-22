import sys
import os
import picamera     # Importing the library for camera module
from time import sleep  # Importing sleep from time library to add delay in program
from datetime import datetime
from fractions import Fraction

preview_time = int(sys.argv[1])
camera = picamera.PiCamera()    # Setting up the camera
now = datetime.now()
print("Preview Duration : "+str(preview_time)+" minutes")
print("Preview Started")
camera.start_preview()
sleep(preview_time * 60)
camera.stop_preview()
print("Preview Stopped")
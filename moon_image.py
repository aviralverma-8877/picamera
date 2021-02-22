import sys
import os
import picamera     # Importing the library for camera module
from time import sleep  # Importing sleep from time library to add delay in program
from datetime import datetime
from fractions import Fraction

number_of_image = int(sys.argv[1])
exposure_time = 3
form = 'png'
print("Taking "+str(number_of_image)+" photos")
camera = picamera.PiCamera()    # Setting up the camera

for i in range(0,number_of_image):
    now = datetime.now()
    print("Capturing image number : "+str(i+1))
    print("Capture save location: "+str(os.path.dirname(os.path.abspath(__file__)))+'/captures/'+str(now)+'-image.'+form)
#    print("Exposure Time: "+str(exposure_time)+" sec")
    camera.resolution = (1920, 1088)                                                                                    #Set resolution
#    camera.framerate = Fraction(1, 6)                                                                                   #Set preview framerate
#    camera.shutter_speed = exposure_time*1000000                                                                        #set exposure time
    camera.iso = 100                                                                                                    #set camera iso
    sleep(1)                                                                                                           #waiting for the camera to adjust the gain
#    camera.exposure_mode = 'off'                                                                                        #turning of the exposure mode
    camera.capture(str(os.path.dirname(os.path.abspath(__file__)))+'/captures/'+str(now)+'-image.'+form,  format=form)  #capture the image

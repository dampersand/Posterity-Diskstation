#!/usr/bin/python3

#Modules
import npyscreen
import operator
import os
import curses
import warnings
from pySMART import *
from megacli import *
from subprocess import Popen, PIPE

#Constants
#It might be good to put stuff like this in MySQL

#General Constants
baseSNs = ['000dfa4406d996272000d8481ec0110b', 'S21TNXAGA08036M', 'S21TNXAH201539J'] 	#serials of /dev/sda and permanent drives
mc = MegaCLI() 																																				#MegaCLI entry point
toasters = 4																																					#Number of slots on the toaster

#Test profile constants
#entries in a given profile should be in the form of {attribute:['value/raw','operator',acceptable range]} such that
#the profile can be read "the device is good if attribute's (value/raw) is (operator) (acceptable range)
#e.g. "for SSDs, the device is good if attribute 177's value is greater than or equal to 19"
#note that SAS profiles are different, as they use megacli instead of pysmart, so there is no 'raw' or 'value'
profiles = {}
profiles['SSD'] = {177:['value', operator.ge, 19], 199:['raw',operator.lt, 1]}
profiles['SATA'] = {1:['raw', operator.lt, 1], 9:['raw', operator.le, 20000], 187:['raw', operator.lt, 1], 198:['raw', operator.lt, 1], 199:['raw', operator.lt, 1], 200:['raw', operator.lt, 1]}
profiles['SATAEnterprise'] = {1:['raw', operator.lt, 1], 9:['raw', operator.le, 30000], 187:['raw', operator.lt, 1], 198:['raw', operator.lt, 1], 199:['raw', operator.lt, 1], 200:['raw', operator.lt, 1]}
profiles['SAS'] = {'media_error_count':[None, operator.lt, 1], 'predictive_failure_count': [None, operator.lt, 1], 'drive_has_flagged_a_smart_alert':[None, operator.eq, False], 'uncorrectable_read_errors':[None, operator.lt, 1], 'uncorrectable_write_errors':[None, operator.lt, 1], 'uncorrectable_verify_errors':[None, operator.lt, 1]} 

#Menu/Grid header constants (includes inverted)
gMenuHeaders = {0:"Rescan", 1:"View disk results", 2: "Delete RAIDs", 3:"Quickwipe", 4:"Quickwipe all", 5:"Zero disk", 6:"Exit"}
gColumnHeaders = {0:"Drive", 1:"Profile", 2:"Serial", 3:"Size", 4:"Pass?"}
gColumnHeadersIndices = {"Drive":0, "Profile":1, "Serial":2, "Size":3, "Pass?":4}


#Notes
#Two drives RAIDed together will produce three devices in the device list - the constituent drives on /dev/bus/0, and the VD on /dev/sdX.


#Helper functions
#Function to change bytes to a human readable format - yoinked from http://stackoverflow.com/a/37423778/6491489
def bytes_2_human_readable(number_of_bytes):
    if number_of_bytes < 0:
        raise ValueError("!!! numberOfBytes can't be smaller than 0 !!!")

    step_to_greater_unit = 1024.

    number_of_bytes = float(number_of_bytes)
    unit = 'bytes'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'KB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'MB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'GB'

    if (number_of_bytes / step_to_greater_unit) >= 1:
        number_of_bytes /= step_to_greater_unit
        unit = 'TB'

    precision = 1
    number_of_bytes = round(number_of_bytes, precision)

    return str(number_of_bytes) + ' ' + unit


#npyscreen class wrappers
class overviewWidget(npyscreen.SimpleGrid): #widget for the drive overview
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		#Create overview-specific constants
		self.columnHeaders = gColumnHeaders
		self.columnHeadersIndices = gColumnHeadersIndices
		self.devlist = []
		self.scanAndTest() #populate drive list and scan all drives

		#Add enter handlers
		self.add_handlers(
			{
				curses.KEY_ENTER: self.h_exit,
				curses.ascii.CR: self.h_exit,
				curses.ascii.NL: self.h_exit
			})

		#remove mouse handler
		if curses.KEY_MOUSE in self.handlers:
			del self.handlers[curses.KEY_MOUSE]

		#get a handle for info widget - it would have been nice to PASS this instead of TAKE it, but I'm having trouble passing it through *args and **kwargs.
		self.info = self.parent.infoDisplay
	
	def scanDevices(hideHidden=True): #Returns a device list of editable devices
		fullDevList = DeviceList()

		#remove /dev/sda and the permanent SSDs
		if hideHidden:
			#reverse order, so we don't mess up the indices from deleted entries
			#'reverse list enumerate' very clear, python.
			for index, device in reversed(list(enumerate(fullDevList.devices))):
				if device.serial in baseSNs:
					del fullDevList.devices[index]

		#sanitize devlist for anything smartctl couldn't find.
		#Add more as this gets worse and worse.
		for device in fullDevList.devices:
			if not device.serial:
				device.serial = "N/A"
			device.warn = False

		#Get device names and add to devlist
		pds = mc.physicaldrives()
		for index, device in reversed(list(enumerate(fullDevList.devices))):
			slot = False
			if 'bus' in device.name.casefold():
				for pd in pds:
					if device.serial.casefold() in pd['inquiry_data']:
						slot = str(pd['slot_number'])
				if not slot:
					device.UIName = "Needs manual testing"
				else:
					device.UIName ='Frontplane Slot ' + slot
			#if it's on the toaster OR is a RAID LD, things are much easier:
			else:
				device.UIName = '/dev/' + device.name
				for toasterNum in range(1,toasters+1):
					if device.name.casefold() in os.path.realpath('/dev/toaster' + str(toasterNum)):
						device.UIName = 'Toaster Slot ' + str(toasterNum)

			#Get device profiles and add to devlist
			#Also delete SAS drives because smartctl and megacli don't play together well
			if device.is_ssd:
				device.profile = "SSD"
			elif 'scsi' in device.interface.casefold():
				device.profile = "RAID"
			elif 'sat' in device.interface.casefold():
				device.profile = "SATA"
			elif 'megaraid' in device.interface.casefold(): #SAS drives - delete them and remake them
				del fullDevList.devices[index]
			else:
				device.profile = ""

		#since MegaCLI and smartctl don't play together well and sometimes give different serial numbers, we now need to scan all SAS drives.
		for index, pd in reversed(list(enumerate(pds))):

			#Hide protected drives
			if hideHidden:
				for serial in baseSNs:
					if serial in pd['inquiry_data']:
						del pds[index]

			#Only doing SAS drives here
			if not 'sas' in pd['pd_type']:
				continue

			#create and populate empty device
			device = Device(None)
			device.serial = pd['inquiry_data'].replace('seagate ', '')
			device.UIName = 'Frontplane Slot ' + str(pd['slot_number'])
			device.profile = 'SAS'
			device.capacity = bytes_2_human_readable(pd['raw_size'])
			device.name = 'bus/0'
			device.devID = pd['device_id']

			#Migrate MegaCLI test params to 'device'
			device.SASattributes = dict()
			device.warn = False
			device.SASattributes['media_error_count'] = pd['media_error_count']
			device.SASattributes['predictive_failure_count'] = pd['predictive_failure_count']
			device.SASattributes['drive_has_flagged_a_smart_alert'] = pd['drive_has_flagged_a_smart_alert']


			#PAUSE: since SAS doesn't support SMART attributes, we need to check the drive's error log and plug output in to pdlist before testing.  Get ready for some weird stuff.
			cmd = Popen(["smartctl", "-l", "error", "-d", "megaraid," + str(pd['device_id']), "/dev/sda"], stdout=PIPE)
			errLog = cmd.communicate()[0].decode("utf-8").split('\n')
			cmd.wait()

			#if error log is shorter than 11 lines, the log should be deformed.  I hope.  Set a warning, but pass the test
			if len(errLog) < 11:
				device.SASattributes['uncorrectable_read_errors'] = -1
				device.SASattributes['uncorrectable_write_errors'] = -1
				device.SASattributes['uncorrectable_verify_errors'] = -1
				device.warn = True

			#if error log is the right size but we can't find read/write/verify,
			else:

				if errLog[8].find('read:') == -1:
					device.SASattributes['uncorrectable_read_errors'] = -1
					device.warn = True
				else:
					device.SASattributes['uncorrectable_read_errors'] = int(errLog[8].split()[7])

				if errLog[9].find('write:') == -1:
					device.SASattributes['uncorrectable_write_errors'] = -1
					device.warn = True
				else:
					device.SASattributes['uncorrectable_write_errors'] = int(errLog[9].split()[7])

				if errLog[10].find('verify:') == -1:
					device.SASattributes['uncorrectable_verify_errors'] = -1
					device.warn = True
				else:
					device.SASattributes['uncorrectable_verify_errors'] = int(errLog[10].split()[7])

			fullDevList.devices.append(device)

		return fullDevList

	def populate(self, devlist): #Puts the devlist into the correct menus on the grid
		self.values = []

		#add devices
		for dev in devlist.devices:
			row = []
			row.append(dev.UIName)
			row.append(dev.profile)
			row.append(dev.serial)
			row.append(dev.capacity)
			row.append(' ')
			self.values.append(row)

		#sort
		self.values.sort(key=lambda x: x[0])

		#create and prepend headers (done AFTER sorting to keep from sorting them in... I don't like it either)
		row = []
		for headerIndex in range(len(self.columnHeaders)):
			row.append(self.columnHeaders[headerIndex])
		self.values.insert(0, [' '])
		self.values.insert(0, row) 

	def testDrive(self, device): #this function tests drives for parameters outlined at the top of this script

		driveRow = False
		for row, line in enumerate(self.values): #check all lines...
			for col, key in enumerate(line): #check all columns in a line...
				if device.serial.casefold() in key.casefold(): #if we find the line for the drive we're looking for
					driveRow = row
					break
				
			if driveRow:
				break

		if not driveRow:
			return #handle this error at some point, for now just quit

		#Handle test profiles
		testPassed = True
		testWarn = False

		#Profile 1: SSD
		if 'SSD' in device.profile:
			for attribute in profiles['SSD']:
				if device.attributes[attribute]: #skip if there's no attribute here
					acceptableParams = profiles['SSD'][attribute]
					if 'int' in type(acceptableParams[2]).__name__:
						if not acceptableParams[1](int(getattr(device.attributes[attribute], acceptableParams[0])), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break
					else:
						if not acceptableParams[1](getattr(device.attributes[attribute], acceptableParams[0]), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break

		#Profile 2: SATA plain... I don't like doing it this way, as it's effectively hardcoding in enterprise hours
		elif 'SATA' in device.profile and int(device.attributes[9].raw) < 20000:
			for attribute in profiles['SATA']:
				if device.attributes[attribute]: #skip if there's no attribute here
					acceptableParams = profiles['SATA'][attribute]
					if 'int' in type(acceptableParams[2]).__name__:
						if not acceptableParams[1](int(getattr(device.attributes[attribute], acceptableParams[0])), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break
					else:
						if not acceptableParams[1](getattr(device.attributes[attribute], acceptableParams[0]), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break

		#Profile 3: SATA enterprise
		elif 'SATA' in device.profile:
			for attribute in profiles['SATAEnterprise']:
				if device.attributes[attribute]: #skip if there's no attribute here
					acceptableParams = profiles['SATAEnterprise'][attribute]
					if 'int' in type(acceptableParams[2]).__name__:
						if not acceptableParams[1](int(getattr(device.attributes[attribute], acceptableParams[0])), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break
					else:
						if not acceptableParams[1](getattr(device.attributes[attribute], acceptableParams[0]), acceptableParams[2]): #tests if the attribute is within acceptable parameters
							testPassed = False
							break

		#Profile 4: SAS - this is unfortunately different than the others, because SAS doesn't do smart attributes so we have to use megacli directly.
		elif 'SAS' in device.profile:

			for attribute in profiles['SAS']:
				acceptableParams = profiles['SAS'][attribute]
				if not acceptableParams[1](device.SASattributes[attribute], acceptableParams[2]):
					testPassed = False
					break

		#Profile 5: RAID - print N/A and return.
		elif 'RAID' in device.profile:
			self.values[driveRow][self.columnHeadersIndices['Pass?']] = 'N/A'
			return

		#No profile specified: return - handle error here ate some other point
		else:
			return



		#test is complete, put results in deviceOverview
		if not testPassed:
			self.values[driveRow][self.columnHeadersIndices['Pass?']] = 'FAIL'
		elif device.warn:
			self.values[driveRow][self.columnHeadersIndices['Pass?']] = 'WARN'
		else:
			self.values[driveRow][self.columnHeadersIndices['Pass?']] = 'PASS'

	def scanAndTest(self): #Function to scan for drive changes, update the UI, and test all drives.
		self.values = []
		self.values.append(['Scanning... Please wait!'])
		self.parent.display()
		self.devlist = self.scanDevices()
		self.populate(self.devlist)
		for drive in self.devlist.devices:
			self.testDrive(drive)
		self.update() #because sometimes this function is called from another object.

	def viewDisk(self): #This function allows you to view the SMART results of the specified disk.
		rowNum = self.diskSelect()

		#if the user selected a non-disk label
		if rowNum < 2:
			return

		#else, start gathering data
		info = []
		serial = self.values[rowNum][self.columnHeadersIndices['Serial']]
		device = False
		profile = self.values[rowNum][self.columnHeadersIndices['Profile']]
		for device in self.devlist.devices:
			if device.serial.casefold() in serial.casefold():
				break
		if not device:
			info.append(['Could not find disk in devlist, consider rescanning first.'])
			self.info.showInfo(info)
			return

		#set up info table
		infoRow = []
		infoRow.append('Attribute ID')
		infoRow.append('Attribute Name')
		infoRow.append('Tested Value')
		info.append(infoRow)
		info.append([' '])

		#RAID Profile:
		if 'RAID' in profile:
			info.append(['RAID Drive - no SMART info'])

		#SAS Profile:
		elif 'SAS' in profile:

			for attribute in profiles['SAS']:
				acceptableParams = profiles['SAS'][attribute]
				infoRow = []
				infoRow.append(' ')
				infoRow.append(attribute)
				infoRow.append(device.SASattributes[attribute])
				info.append(infoRow)

		#SATA Profile:
		elif 'SATA' or 'SSD' in profile:

			for attribute in profiles[profile]:
				if device.attributes[attribute]: #only print what exists on the device
					infoRow = []
					acceptableParams = profiles[profile][attribute]
					infoRow.append(attribute)
					infoRow.append(getattr(device.attributes[attribute], 'name'))
					infoRow.append(getattr(device.attributes[attribute], acceptableParams[0]))
					info.append(infoRow)

		#Didn't find anything?
		else:
			info.append(['No profile selected - uncertain of test parameters.'])


		self.info.showInfo(info)

	def quickWipeDisk(self, rowNum = 0, suppressMessages=False): #function to initialize a quickwipe of a disk

		if not suppressMessages:
			rowNum = self.diskSelect()

		#if the user selected a non-disk label
		if rowNum < 2:
			return

		#Are you sure you want to continue?
		if not suppressMessages:
			message = "You are about to quickwipe " + self.values[rowNum][self.columnHeadersIndices['Drive']] +'.\n\nThis will dump its partition table or, in the case of a device on the frontplane, it will fast-re-init the drive.  It is much faster than zeroing a disk and results in fewer writes, but it is NOT data-destructive.\n\nWould you like to continue?'
			confirm = npyscreen.notify_yes_no(message, title="Quickwipe?", editw = 1)

			if not confirm:
				return

		#if we're continuing, collect info
		pds = mc.physicaldrives()
		UIName = self.values[rowNum][self.columnHeadersIndices['Drive']]
		serial = self.values[rowNum][self.columnHeadersIndices['Serial']]
		device = False
		profile = self.values[rowNum][self.columnHeadersIndices['Profile']]
		backupValues = self.values
		for device in self.devlist.devices:
			if device.serial.casefold() in serial.casefold():
				break
		if not device:
			info = ['Could not find disk in devlist, consider rescanning first.']
			self.info.showInfo(info)
			return UIName
		name = device.name

		#If it's a RAID device
		if 'RAID' in profile:
			if not suppressMessages:
				message = "Er, nope, this is a RAID drive.  Don't quickwipe a RAID.  Use the 'delete RAIDs' option, or delete it yourself (if there's another RAID you want to save)"
				npyscreen.notify_confirm(message, title="Failure!", editw = 1)
			return UIName

		#Wipe device on the frontplane:
		elif 'bus' in name.casefold():
			#find the device in megacli
			mcDevice = False
			for pd in pds:
				if serial.casefold() in pd['inquiry_data']:
					mcDevice = pd
					break

			#didn't find a megacli device?
			if not mcDevice:
				if not suppressMessages:
					message = 'Could not find disk in megacli.'
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#device already in a RAID?
			if 'drive_position' in mcDevice.keys():
				if not suppressMessages:
					message = 'This device is still in a RAID.  You need to delete those RAID drives before I can wipe this disk.'
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#Continue moving forward by gathering slot and enclosure info
			slot = str(mcDevice['slot_number'])
			enclosure = str(mcDevice['enclosure_id'])

			#Device set to bad?
			if ((mcDevice['firmware_state'] != 'online, spun up') and (mcDevice['firmware_state'] != 'unconfigured(good), spun up')):
				try:
					mc.make_pd_good(enclosure + ':' + slot, mcDevice['adapter_id'])
				except:
					if not suppressMessages:
						message = 'The device appears to be set to bad (I think), but I can not set it to good.  I will try to continue anyway, but if the wipe fails, this step might be the problem.'
						npyscreen.notify_confirm(message, title="Information!", editw = 1)

			#device in foreign?
			if mcDevice['foreign_state']:
				if not suppressMessages:
					message = "I see a foreign state on this device.  MegaCLI can't clear a single foreign state, it can only clear EVERY foreign state on the adapter.  Is this okay?"
					confirm = npyscreen.notify_yes_no(message, title="Clear Foreign?", editw = 1)
					if not confirm:
						return
				try:
					mc.clear_foreign(mcDevice['adapter_id'])
				except:
					if not suppressMessages:
						message = 'Something went wrong when clearing the foreign state.  I will try to continue anyway, but if the wipe fails, this step might be the problem.'

			#At this point, we're good to create a RAID
			try:
				result = mc.create_ld(0,[enclosure + ':' + slot], mcDevice['adapter_id'], force = True)
				vd = int(result[1].split('vd ', 1)[1])
			except:
				if not suppressMessages:
					message = "Couldn't create a dummy VD to wipe.  Aborting..."
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#Now we reinit the device.  We will need to display a 'please wait' screen here, and the info widget won't do.  Use the backupValues to do this without losing stuff.
			try:
				self.values = []
				self.values.append(['Wiping ' + UIName])
				self.update()
				self.parent.display()
				mc.start_init(vd, mcDevice['adapter_id'])
				while True:
					curses.napms(5000)
					result = mc.check_init(vd, mcDevice['adapter_id'])
					if 'not in progress' in result[1]:
						break
			except:
				self.values = backupValues
				self.update()
				self.parent.display()
				if not suppressMessages:
					message = "Couldn't wipe the drive.  Aborting..."
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#reset values in case we're being looped
			self.values = backupValues

			#Finally, delete the ld
			try:
				mc.remove_ld(vd, mcDevice['adapter_id'], force=True)
			except:
				if not suppressMessages:
					message = "Couldn't delete dummy LD (but I think the wipe may have worked).  Aborting..."
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#If we made it to this point, we've succeeded.
			if not suppressMessages:
				message = "Drive successfully wiped!"
				npyscreen.notify_confirm(message, title="Success!", editw = 1)

				#Don't rescan if this is being run multiple times - it'll speed things up.
				self.scanAndTest()

		#Otherwise, we're not in the frontplane, we're on the toaster - name should be sda/sdb/sdc etc
		else:
			name = '/dev/' + name
			try:
				self.values = []
				self.values.append(['Wiping ' + UIName])
				self.update()
				self.parent.display()
				os.system("wipefs -a " + name + " &>/dev/null")
				os.system("dd if=/dev/zero of=" + name + " bs=512 count=50 &>/dev/null")
			except:
				self.values = backupValues
				self.update()
				self.parent.display()
				if not suppressMessages:
					message = "Failed to wipe drive."
					npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#replace values in case we're being looped
			self.values = backupValues

			if not suppressMessages:
				message = "Drive successfully wiped!"
				npyscreen.notify_confirm(message, title="Success!", editw = 1)

			#update because npyscreens might not.
			self.update()
			self.parent.display()

	def quickWipeAll(self): #Quickwipes all drives
		#check resolve
		message = "I will now attempt to quickwipe EVERY drive on this list!  Once you hit ok, there's no going back!"
		confirm = npyscreen.notify_ok_cancel(message, title="Quick Wipe All", editw = 1)
		if not confirm:
			return

		#Init error watcher
		errors = []
		for num in range(len(self.values)):
			errors.append(self.quickWipeDisk(rowNum = num, suppressMessages = True))

		#Start building any error message
		cleanErrors = []
		for error in errors:
			if error:
				cleanErrors.append(error)

		if len(cleanErrors) > 0:
			for error in cleanErrors:
				message = error + "\n"

			message = "Quickwipe is complete, but the following disks ran into problems.  Investigate and re-wipe the following:\n" + message
			npyscreen.notify_confirm(message, title="ErrorList", editw = 1)
		else:
			npyscreen.notify_confirm("Quickwipe completed successfully!", title="Success!", editw = 1)

		#After wiping all, rescan all
		self.scanAndTest()

	def diskSelect(self): #this function enables the overview box and lets the user select a row.  It returns the row number.
		self.set_editable=True
		self.edit()
		self.set_editable=False
		return self.edit_cell[0]

	def wipeRAID(self): #this function will wipe all the RAID drives that aren't our protected drive.

		#test resolve
		message = "This will wipe every RAID drive on the list!  It's important to note that the OS is installed on a RAID drive, and I can't protect the OS drive by serial (thanks, MegaCLI).  I have to protect it by ASSUMING it's ld #0 on adapter 0... so if it isn't, we're going to have a bad time.  Please verify before pressing OK!"
		confirm = npyscreen.notify_ok_cancel(message, title="Very bad thing could happen!", editw = 1)

		if not confirm:
			return

		message = "I'm serious!  You can check LD0 in megacli if you need to.  I'll wait! The command is\n /opt/MegaRAID/MegaCli/MegaCli64 -LDInfo -L1 -a0"
		confirm = npyscreen.notify_ok_cancel(message, title="Not kidding!", editw = 1)

		if not confirm:
			return

		#if we're going for it, assemble information
		lds = mc.logicaldrives()
		for ld in lds:
			if ld['id'] == 0: #if the user is trying to poke ld0, ignore it.  Keep that ld.  it's important.
				continue
			else:
				try:
					mc.remove_ld(ld['id'], ld['adapter_id'], force=True)
				except:
					npyscreen.notify_confirm("Something went wrong in MegaCLI when destroying ld " + ld['id'], title="Something failed", editw = 1)

		npyscreen.notify_confirm("RAID drives destroyed!", title="Success!", editw = 1)
		self.scanAndTest()

	def fullWipeDisk(self): #this function will zero out a particular disk.  It will take for freakin' ever.
		rowNum = self.diskSelect()

		#if the user selected a non-disk label
		if rowNum < 2:
			return

		#test resolve
		message = "You are about to zero out " + self.values[rowNum][self.columnHeadersIndices['Drive']] +'.\n\nThis will zero all bits on the disk (or in the case of a device on the frontplane, perform a full initialization).  It is data-destructive.  It will also take a LONG, LONG time, during which this system will be unresponsive.\n\nWould you like to continue?'
		confirm = npyscreen.notify_yes_no(message, title="Zero disk?", editw = 1)

		if not confirm:
			return

		#gather information about the drive
		pds = mc.physicaldrives()
		UIName = self.values[rowNum][self.columnHeadersIndices['Drive']]
		serial = self.values[rowNum][self.columnHeadersIndices['Serial']]
		device = False
		profile = self.values[rowNum][self.columnHeadersIndices['Profile']]
		backupValues = self.values

		for device in self.devlist.devices:
			if device.serial.casefold() in serial.casefold():
				break
		if not device:
			info = ['Could not find disk in devlist, consider rescanning first.']
			self.info.showInfo(info)
			return UIName
		name = device.name

		#If it's a RAID device
		if 'RAID' in profile:
			message = "Er, nope, this is a RAID drive.  Don't quickwipe a RAID.  Use the 'delete RAIDs' option, or delete it yourself (if there's another RAID you want to save)"
			npyscreen.notify_confirm(message, title="Failure!", editw = 1)
			return UIName

		#Wipe device on the frontplane:
		elif 'bus' in name.casefold():
			#find the device in megacli
			mcDevice = False
			for pd in pds:
				if serial.casefold() in pd['inquiry_data']:
					mcDevice = pd
					break

			#didn't find a megacli device?
			if not mcDevice:
				message = 'Could not find disk in megacli.'
				npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#device already in a RAID?
			if 'drive_position' in mcDevice.keys():
				message = 'This device is still in a RAID.  You need to delete those RAID drives before I can wipe this disk.'
				npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#Continue moving forward by gathering slot and enclosure info
			slot = str(mcDevice['slot_number'])
			enclosure = str(mcDevice['enclosure_id'])

			#Device set to bad?
			if ((mcDevice['firmware_state'] != 'online, spun up') and (mcDevice['firmware_state'] != 'unconfigured(good), spun up')):
				try:
					mc.make_pd_good(enclosure + ':' + slot, mcDevice['adapter_id'])
				except:
					message = 'The device appears to be set to bad (I think), but I can not set it to good.  I will try to continue anyway, but if the wipe fails, this step might be the problem.'
					npyscreen.notify_confirm(message, title="Information!", editw = 1)

			#device in foreign?
			if mcDevice['foreign_state']:
				message = "I see a foreign state on this device.  MegaCLI can't clear a single foreign state, it can only clear EVERY foreign state on the adapter.  Is this okay?"
				confirm = npyscreen.notify_yes_no(message, title="Clear Foreign?", editw = 1)
				if not confirm:
					return
				try:
					mc.clear_foreign(mcDevice['adapter_id'])
				except:
					message = 'Something went wrong when clearing the foreign state.  I will try to continue anyway, but if the wipe fails, this step might be the problem.'

			#Now we init the device.  We will need to display a 'please wait' screen here, and the info widget won't do.  Use the backupValues to do this without losing stuff.
			try:
				self.values = []
				self.values.append(['Zeroing ' + UIName])
				self.update()
				self.parent.display()
				mc.start_init(vd, mcDevice['adapter_id'], full = True)
				while True:
					curses.napms(5000)
					result = mc.check_init(vd, mcDevice['adapter_id'])
					if 'not in progress' in result[1]:
						break
			except:
				self.values = backupValues
				self.update()
				self.parent.display()
				message = "Couldn't zero the drive.  Aborting..."
				npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#reset values
			self.values = backupValues

			#If we made it to this point, we've succeeded.
			message = "Drive successfully zeroed!"
			npyscreen.notify_confirm(message, title="Success!", editw = 1)
			self.scanAndTest()


		#Otherwise, we're not in the frontplane, we're on the toaster - name should be sda/sdb/sdc etc
		else:
			name = '/dev/' + name
			try:
				self.values = []
				self.values.append(['Zeroing ' + UIName])
				self.update()
				self.parent.display()
				os.system("wipefs -a " + name + " &>/dev/null")
				os.system("dd if=/dev/zero of=" + name + " bs=512 &>/dev/null")
			except:
				self.values = backupValues
				self.update()
				self.parent.display()
				message = "Failed to zero drive."
				npyscreen.notify_confirm(message, title="Failure!", editw = 1)
				return UIName

			#replace values and proclaim our victory
			self.values = backupValues
			message = "Drive successfully wiped!"
			npyscreen.notify_confirm(message, title="Success!", editw = 1)

			#update because npyscreens might not.
			self.update()
			self.parent.display()

	def custom_print_cell(self, actual_cell, cell_display_value): #Sets colors of the 'pass/fail' column
		if cell_display_value == "FAIL":
			actual_cell.color = "DANGER"
		elif cell_display_value == "PASS":
			actual_cell.color = "GOOD"
		elif cell_display_value == "N/A":
			actual_cell.color = "WARNING"
		elif cell_display_value == "WARN":
			actual_cell.color = "WARNING"
		else:
			actual_cell.color = "DEFAULT"


class menuWidget(npyscreen.SimpleGrid): #widget for the main (horizontal) menu.  This is a pain to work with, should maybe be a menu widget.
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		#because apparently the only way for a peer widget to be passed is to take it.  So much for object-orientation.  wtf python
		self.overview = self.parent.driveOverview
		self.menuHeaders = gMenuHeaders
		self.populate()

		#Add event handlers for enter key
		self.add_handlers(
			{
				curses.KEY_ENTER: self.h_enterPressed,
				curses.ascii.CR: self.h_enterPressed,
				curses.ascii.NL: self.h_enterPressed
			})

		#remove mouse handler
		if curses.KEY_MOUSE in self.handlers:
			del self.handlers[curses.KEY_MOUSE]
	
	def populate(self): #Place menu entries in the menu
		self.values = []

		#add menu entries
		row = []
		for headerIndex in range(len(self.menuHeaders)):
			row.append(self.menuHeaders[headerIndex])
		
		self.values.append(row)

	def h_enterPressed(self, inpt): #If enter is pressed on the menu, do something.
		cell_x = self.edit_cell[1]
		selection = self.menuHeaders[cell_x]

		if 'Rescan' in selection:
			self.overview.scanAndTest()

		elif 'View disk' in selection:
			self.overview.viewDisk()

		elif 'Exit' in selection:
			self.parent.parentApp.switchForm(None)

		#must call this one directly because there also exists a 'Quickwipe all'
		elif selection == 'Quickwipe':
			self.overview.quickWipeDisk()

		elif selection == 'Quickwipe all':
			self.overview.quickWipeAll()

		elif 'Delete RAIDs' in selection:
			self.overview.wipeRAID()

		elif 'Zero disk' in selection:
			self.overview.fullWipeDisk()


class infoWidget(npyscreen.SimpleGrid):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

		#remove mouse handler
		if curses.KEY_MOUSE in self.handlers:
			del self.handlers[curses.KEY_MOUSE]

	def set_up_handlers(self):
		self.handlers = {
			curses.ascii.NL:	self.h_exit,
			curses.ascii.CR:	self.h_exit,
			curses.ascii.ESC:	self.h_exit_escape,
			curses.KEY_ENTER:	self.h_exit
		}

		self.complex_handlers = []

	def showInfo(self, incomingValues):
		self.values = []

		for entry in incomingValues:
			self.values.append(entry)

		self.hidden=False
		self.edit()
		self.hidden=True
		self.parent.display()

class mainForm(npyscreen.FormBaseNew): #Main form for organizing widgets
	def create(self):

		#Add an info display
		self.infoDisplay = self.add(infoWidget, column_width = 30, hidden=True, editable=True)

		#create the drive overview grid
		#note that if max_height is too HIGH, driveoverview won't show at all.  If you're running this on a machine other than the one
		#I tested on, this might be a problem.  woo npycurses, eff dynamic height determination.
		self.nextrely = 2
		self.driveOverview = self.add(overviewWidget, column_width=26, name="Drive Overview", editable=False, select_whole_line=True, max_height=19)

		#Add a menu
		#I've looked all over... I don't see a way to dynamically set these values, so they may look like crap
		#on another machine.
		#self.nextrely -= 2
		self.nextrelx += 5
		self.menu = self.add(menuWidget, column_width=19, name="Main Menu", editable=True)

	def afterEditing(self): #Kills program once this form is done being edited
		self.parentApp.setNextForm(None)




#NPSAppManaged is a framework to start and end the application while managing displays.  This is
#every form's entry point.
#effectively, this is a wrapper for a 'main' loop.  YOU DEED IT
class applicationClass(npyscreen.NPSAppManaged):
	def onStart(self):
		F = self.addForm('MAIN', mainForm, name="Hard Drive Station")

#Only run if we were explicitly called
if __name__ == '__main__':
	warnings.filterwarnings("ignore")
	mainWindow = applicationClass().run()

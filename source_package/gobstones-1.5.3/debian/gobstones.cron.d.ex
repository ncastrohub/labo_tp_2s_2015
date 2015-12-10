#
# Regular cron jobs for the gobstones package
#
0 4	* * *	root	[ -x /usr/bin/gobstones_maintenance ] && /usr/bin/gobstones_maintenance

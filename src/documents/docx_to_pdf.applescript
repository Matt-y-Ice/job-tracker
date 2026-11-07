on run argv
	try
		tell application "Microsoft Word" to get version
	on error
		log "WORD_MISSING"
		return
	end try
	tell application "Microsoft Word"
		repeat with i from 1 to (count of argv) by 2
			set inPath to item i of argv
			set outPath to item (i + 1) of argv
			open POSIX file inPath
			set theDoc to active document
			save as theDoc file name outPath file format format PDF
			close theDoc saving no
		end repeat
	end tell
end run

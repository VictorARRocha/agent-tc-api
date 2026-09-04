Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
BaseDir = Fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run Chr(34) & BaseDir & "\run_agent_tc_api.bat" & Chr(34), 0, False

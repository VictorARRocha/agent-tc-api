' Roda o vigia_docker.ps1 sem abrir janela (usado pela tarefa "AgenteTC - Vigia Docker").
Set WshShell = CreateObject("WScript.Shell")
Set Fso = CreateObject("Scripting.FileSystemObject")
BaseDir = Fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & BaseDir & "\vigia_docker.ps1" & Chr(34), 0, True

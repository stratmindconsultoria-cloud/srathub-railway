' Inicia o hub sem janela (usado no inicio automatico com o Windows)
Set fso = CreateObject("Scripting.FileSystemObject")
raiz = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = raiz
sh.Run """" & raiz & "\.venv\Scripts\pythonw.exe"" -m hub", 0, False

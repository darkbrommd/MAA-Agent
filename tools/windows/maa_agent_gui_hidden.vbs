Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ToolsDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BaseDir = FSO.GetAbsolutePathName(FSO.BuildPath(ToolsDir, "..\.."))
WshShell.CurrentDirectory = BaseDir
PythonW = FSO.BuildPath(BaseDir, ".venv\Scripts\pythonw.exe")
GuiPy = FSO.BuildPath(BaseDir, "app\maa_agent_gui.py")
Args = ""
For Each Arg In WScript.Arguments
  Args = Args & " " & Arg
Next
WshShell.Run """" & PythonW & """ """ & GuiPy & """" & Args, 0, False

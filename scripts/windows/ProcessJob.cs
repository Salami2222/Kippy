using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

// The scheduler owns the supervisor; its handle owns the complete child process tree.
public static class MellowProcessJob {
    [StructLayout(LayoutKind.Sequential)] struct Startup {
        public uint cb; public IntPtr reserved, desktop, title;
        public uint x,y,xs,ys,xc,yc,fill,flags; public ushort show,reserved2;
        public IntPtr reserved3,input,output,error;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process,thread; public uint processId,threadId; }
    [StructLayout(LayoutKind.Sequential)] struct BasicLimit {
        public long processTime,jobTime; public uint flags; public UIntPtr min,max;
        public uint active; public UIntPtr affinity; public uint priority,scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong a,b,c,d,e,f; }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit {
        public BasicLimit basic; public IoCounters io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes,string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int type,IntPtr info,uint length);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref Startup startup,out ProcessInfo info);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process,uint code);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    static void Check(bool ok) { if(!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    public static int Run(string executable,string arguments,string directory) {
        IntPtr job=CreateJobObject(IntPtr.Zero,null), memory=IntPtr.Zero;
        ProcessInfo process=new ProcessInfo(); bool assigned=false;
        if(job==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            ExtendedLimit limits=new ExtendedLimit(); limits.basic.flags=0x2000; // KILL_ON_JOB_CLOSE
            int size=Marshal.SizeOf(limits); memory=Marshal.AllocHGlobal(size);
            Marshal.StructureToPtr(limits,memory,false);
            Check(SetInformationJobObject(job,9,memory,(uint)size));
            Startup startup=new Startup(); startup.cb=(uint)Marshal.SizeOf(startup);
            // Suspend before assignment so the venv launcher cannot create an escaping child.
            Check(CreateProcess(executable,new StringBuilder("\""+executable+"\" "+arguments),IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,directory,ref startup,out process));
            Check(AssignProcessToJobObject(job,process.process)); assigned=true;
            if(ResumeThread(process.thread)==0xffffffff) throw new Win32Exception(Marshal.GetLastWin32Error());
            Check(WaitForSingleObject(process.process,0xffffffff)==0);
            uint code; Check(GetExitCodeProcess(process.process,out code)); return unchecked((int)code);
        } finally {
            if(!assigned && process.process!=IntPtr.Zero) TerminateProcess(process.process,1);
            if(memory!=IntPtr.Zero) Marshal.FreeHGlobal(memory);
            CloseHandle(job); // Also removes any surviving descendants after a normal parent exit.
            if(process.thread!=IntPtr.Zero) CloseHandle(process.thread);
            if(process.process!=IntPtr.Zero) CloseHandle(process.process);
        }
    }
}

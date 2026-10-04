import Cocoa
import WebKit
import Security
import Darwin

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    var window: NSWindow!; var web: WKWebView!; var server: Process?; var port: UInt16 = 0
    var token = ""; var dataURL: URL!; var lockFD: Int32 = -1; var attempts = 0
    var isQuitting = false; var downloads = [WKDownload]()
    func applicationDidFinishLaunching(_ notification: Notification) {
        do { try start() } catch { fail("Fieldwork could not start", error.localizedDescription) }
    }
    func start() throws {
        let fm = FileManager.default
        let configured = ProcessInfo.processInfo.environment["FIELDWORK_TEST_DATA_DIR"]
        dataURL = configured.map { URL(fileURLWithPath:$0,isDirectory:true) } ?? fm.urls(for:.applicationSupportDirectory,in:.userDomainMask)[0].appendingPathComponent("Fieldwork",isDirectory:true)
        try fm.createDirectory(at:dataURL,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        lockFD = Darwin.open(dataURL.appendingPathComponent("desktop.lock").path,O_CREAT|O_RDWR,0o600)
        guard lockFD >= 0, flock(lockFD,LOCK_EX|LOCK_NB) == 0 else { throw NSError(domain:"Fieldwork",code:1,userInfo:[NSLocalizedDescriptionKey:"Fieldwork is already using this workspace. Reopen its existing window from the Dock."]) }
        port = try freePort()
        var bytes=[UInt8](repeating:0,count:32); guard SecRandomCopyBytes(kSecRandomDefault,bytes.count,&bytes)==errSecSuccess else { throw NSError(domain:"Fieldwork",code:2) }
        token=bytes.map{String(format:"%02x",$0)}.joined()
        let resources=Bundle.main.resourceURL!
        let executable=resources.appendingPathComponent("backend/fieldwork-server")
        guard fm.isExecutableFile(atPath:executable.path) else { throw NSError(domain:"Fieldwork",code:3,userInfo:[NSLocalizedDescriptionKey:"The bundled processing engine is missing. Reinstall Fieldwork from the original disk image."]) }
        let process=Process(); process.executableURL=executable; process.currentDirectoryURL=resources
        var env=ProcessInfo.processInfo.environment
        env["FIELDWORK_PORT"]=String(port); env["FIELDWORK_TOKEN"]=token;env["FIELDWORK_DATA_DIR"]=dataURL.path
        env["FIELDWORK_OCR_BINARY"]=resources.appendingPathComponent("fieldwork-ocr").path
        env["PYTHONNOUSERSITE"]="1";env["PATH"]="/usr/bin:/bin:/usr/sbin:/sbin"; env.removeValue(forKey:"PYTHONPATH");env.removeValue(forKey:"PYTHONHOME")
        process.environment=env
        // Backend access logging is disabled; no bootstrap URLs or data are logged here.
        let logURL=dataURL.appendingPathComponent("application.log")
        if let attrs=try? fm.attributesOfItem(atPath:logURL.path),let size=attrs[.size] as? NSNumber,size.intValue > 2_000_000 { try? fm.removeItem(at:logURL) }
        if !fm.fileExists(atPath:logURL.path) { fm.createFile(atPath:logURL.path,contents:nil,attributes:[.posixPermissions:0o600]) }
        let log=try FileHandle(forWritingTo:logURL);try log.seekToEnd();process.standardOutput=log;process.standardError=log
        process.terminationHandler={ [weak self] p in DispatchQueue.main.async { if let self, !self.isQuitting { self.fail("The processing engine stopped", "Your saved workspace is unchanged. Quit and reopen Fieldwork. Diagnostic log: Application Support/Fieldwork/application.log") } } }
        server=process;try process.run(); createWindow(); pollReady()
    }
    func freePort() throws -> UInt16 {
        let fd=socket(AF_INET,SOCK_STREAM,0);guard fd>=0 else { throw NSError(domain:"Fieldwork",code:4) };defer{close(fd)}
        var addr=sockaddr_in();addr.sin_len=UInt8(MemoryLayout<sockaddr_in>.size);addr.sin_family=sa_family_t(AF_INET);addr.sin_port=0;addr.sin_addr=in_addr(s_addr:inet_addr("127.0.0.1"))
        let bound=withUnsafePointer(to:&addr){$0.withMemoryRebound(to:sockaddr.self,capacity:1){Darwin.bind(fd,$0,socklen_t(MemoryLayout<sockaddr_in>.size))}}
        guard bound==0 else { throw NSError(domain:"Fieldwork",code:5) }
        var len=socklen_t(MemoryLayout<sockaddr_in>.size)
        _=withUnsafeMutablePointer(to:&addr){$0.withMemoryRebound(to:sockaddr.self,capacity:1){getsockname(fd,$0,&len)}}
        return UInt16(bigEndian:addr.sin_port)
    }
    func createWindow() {
        let config=WKWebViewConfiguration();config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self,name:"fieldwork")
        web=WKWebView(frame:.zero,configuration:config);web.navigationDelegate=self;web.uiDelegate=self
        web.allowsBackForwardNavigationGestures=false
        let screen=NSScreen.main?.visibleFrame ?? NSRect(x:0,y:0,width:1440,height:900)
        window=NSWindow(contentRect:NSRect(x:0,y:0,width:min(1400,screen.width-60),height:min(900,screen.height-60)),styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        window.isReleasedWhenClosed=false;window.minSize=NSSize(width:960,height:680);window.title="Fieldwork";window.contentView=web;window.center();window.setFrameAutosaveName("FieldworkMainWindow");window.makeKeyAndOrderFront(nil)
        web.loadHTMLString("<html><body style='font:18px -apple-system;background:#f6f8f6;padding:60px'><h1>Fieldwork</h1><p>Starting your private document workspace…</p></body></html>",baseURL:nil)
        makeMenus();NSApp.activate(ignoringOtherApps:true)
    }
    func makeMenus() {
        let menu=NSMenu();NSApp.mainMenu=menu
        let appItem=NSMenuItem();menu.addItem(appItem);let appMenu=NSMenu();appItem.submenu=appMenu
        appMenu.addItem(withTitle:"About Fieldwork",action:#selector(about),keyEquivalent:"");appMenu.addItem(.separator())
        appMenu.addItem(withTitle:"Hide Fieldwork",action:#selector(NSApplication.hide(_:)),keyEquivalent:"h");appMenu.addItem(.separator())
        appMenu.addItem(withTitle:"Quit Fieldwork",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        let fileItem=NSMenuItem();menu.addItem(fileItem);let fileMenu=NSMenu(title:"File");fileItem.submenu=fileMenu
        fileMenu.addItem(withTitle:"Import Documents…",action:#selector(requestImport),keyEquivalent:"o")
        fileMenu.addItem(withTitle:"Show Workspace in Finder",action:#selector(showWorkspace),keyEquivalent:"")
        let editItem=NSMenuItem();menu.addItem(editItem);let edit=NSMenu(title:"Edit");editItem.submenu=edit
        for (title,action,key) in [("Undo","undo:","z"),("Cut","cut:","x"),("Copy","copy:","c"),("Paste","paste:","v"),("Select All","selectAll:","a")] {edit.addItem(withTitle:title,action:Selector(action),keyEquivalent:key)}
        let windowItem=NSMenuItem();menu.addItem(windowItem);let wm=NSMenu(title:"Window");windowItem.submenu=wm
        wm.addItem(withTitle:"Show Fieldwork",action:#selector(showWindow),keyEquivalent:"0")
        let helpItem=NSMenuItem();menu.addItem(helpItem);let hm=NSMenu(title:"Help");helpItem.submenu=hm
        hm.addItem(withTitle:"Fieldwork Help",action:#selector(help),keyEquivalent:"?")
        for m in [appMenu,fileMenu,wm,hm] {for i in m.items {if i.target==nil && i.action != #selector(NSApplication.terminate(_:)) && i.action != #selector(NSApplication.hide(_:)) {i.target=self}}}
    }
    @objc func about(){NSApp.orderFrontStandardAboutPanel(options:[.applicationName:"Fieldwork",.applicationVersion:Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "1.0.0",.credits:NSAttributedString(string:"Private document extraction. Offline Apple Vision OCR.\nWatch folders run while Fieldwork is running.\nCopyright © 2026 Giovanni Zanette. MIT licensed.")])}
    @objc func requestImport(){web.evaluateJavaScript("window.dispatchEvent(new Event('fieldwork-request-import'))",completionHandler:nil);showWindow()}
    @objc func showWorkspace(){NSWorkspace.shared.open(dataURL)}
    @objc func showWindow(){window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)}
    @objc func help(){let alert=NSAlert();alert.messageText="Fieldwork on your Mac";alert.informativeText="Import a document, teach a template, process a batch, review issues, and approve rows for export. OCR results require source review.\n\nWatch folders run while the app remains open, even when the window is closed. Quit stops processing; reopen resumes your workspace.\n\nYour files stay in Application Support/Fieldwork. Use the in-app backup tool before moving computers. No cloud service or account is required.";alert.runModal()}
    func pollReady(){
        attempts+=1
        URLSession.shared.dataTask(with:URL(string:"http://127.0.0.1:\(port)/api/health")!){[weak self] data,response,error in
            DispatchQueue.main.async {guard let self,!self.isQuitting else{return}
                if let data,let obj=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any],obj["application"] as? String == "fieldwork", obj["ready"] as? Bool == true {
                    self.web.load(URLRequest(url:URL(string:"http://127.0.0.1:\(self.port)/?token=\(self.token)")!)); self.token=""
                } else if self.attempts<150 {DispatchQueue.main.asyncAfter(deadline:.now()+0.2){self.pollReady()}}
                else {self.fail("Fieldwork took too long to start","Quit and reopen the app. Your existing files have not been changed.")}
            }
        }.resume()
    }
    func local(_ url:URL?) -> Bool {guard let url else{return false};return url.scheme=="http" && url.host=="127.0.0.1" && url.port==Int(port)}
    func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping(WKNavigationActionPolicy)->Void){
        if action.request.url?.absoluteString.hasPrefix("blob:http://127.0.0.1:\(port)/") == true {decisionHandler(.download);return}
        if local(action.request.url){decisionHandler(action.shouldPerformDownload ? .download : .allow);return}
        if action.request.url?.absoluteString=="about:blank" {decisionHandler(.allow);return}
        if action.navigationType == .linkActivated,let url=action.request.url,["https","mailto"].contains(url.scheme ?? ""){NSWorkspace.shared.open(url)}
        decisionHandler(.cancel)
    }
    func webView(_ webView:WKWebView,decidePolicyFor response:WKNavigationResponse,decisionHandler:@escaping(WKNavigationResponsePolicy)->Void){
        if response.response.url?.absoluteString.hasPrefix("blob:http://127.0.0.1:\(port)/") == true {decisionHandler(.download);return}
        guard local(response.response.url) else{decisionHandler(.cancel);return}
        let disposition=(response.response as? HTTPURLResponse)?.value(forHTTPHeaderField:"Content-Disposition") ?? ""
        decisionHandler(disposition.lowercased().contains("attachment") || !response.canShowMIMEType ? .download : .allow)
    }
    func webView(_ webView:WKWebView,createWebViewWith configuration:WKWebViewConfiguration,for action:WKNavigationAction,windowFeatures:WKWindowFeatures)->WKWebView?{if local(action.request.url){webView.load(action.request)};return nil}
    func webView(_ webView:WKWebView,runOpenPanelWith parameters:WKOpenPanelParameters,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping([URL]?)->Void){
        guard frame.isMainFrame,local(frame.request.url) else{completionHandler(nil);return}
        let panel=NSOpenPanel();panel.canChooseDirectories=false;panel.canChooseFiles=true;panel.allowsMultipleSelection=parameters.allowsMultipleSelection
        panel.beginSheetModal(for:window){result in completionHandler(result == .OK ? panel.urls:nil)}
    }
    func webView(_ webView:WKWebView,runJavaScriptAlertPanelWithMessage message:String,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping()->Void){
        guard frame.isMainFrame,local(frame.request.url) else{completionHandler();return}
        let alert=NSAlert();alert.messageText="Fieldwork";alert.informativeText=message
        alert.beginSheetModal(for:window){_ in completionHandler()}
    }
    func webView(_ webView:WKWebView,runJavaScriptConfirmPanelWithMessage message:String,initiatedByFrame frame:WKFrameInfo,completionHandler:@escaping(Bool)->Void){
        guard frame.isMainFrame,local(frame.request.url) else{completionHandler(false);return}
        let alert=NSAlert();alert.messageText="Fieldwork";alert.informativeText=message;alert.addButton(withTitle:"Continue");alert.addButton(withTitle:"Cancel")
        alert.beginSheetModal(for:window){result in completionHandler(result == .alertFirstButtonReturn)}
    }
    func userContentController(_ controller:WKUserContentController,didReceive message:WKScriptMessage){
        guard message.frameInfo.isMainFrame,local(message.frameInfo.request.url),let body=message.body as? [String:Any],body["action"] as? String == "chooseWatchFolder" else{return}
        let panel=NSOpenPanel();panel.title="Choose a folder to watch while Fieldwork is running";panel.canChooseDirectories=true;panel.canChooseFiles=false;panel.allowsMultipleSelection=false
        panel.beginSheetModal(for:window){[weak self] result in guard let self,result == .OK,let url=panel.url else{return}
            guard let data=try? JSONSerialization.data(withJSONObject:["path":url.path]),let json=String(data:data,encoding:.utf8) else{return}
            self.web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('fieldwork-folder-selected',{detail:\(json)}))",completionHandler:nil)
        }
    }
    func webView(_ webView:WKWebView,navigationAction:WKNavigationAction,didBecome download:WKDownload){download.delegate=self;downloads.append(download)}
    func webView(_ webView:WKWebView,navigationResponse:WKNavigationResponse,didBecome download:WKDownload){download.delegate=self;downloads.append(download)}
    func download(_ download:WKDownload,decideDestinationUsing response:URLResponse,suggestedFilename:String,completionHandler:@escaping(URL?)->Void){
        let panel=NSSavePanel();panel.nameFieldStringValue=URL(fileURLWithPath:suggestedFilename).lastPathComponent;panel.canCreateDirectories=true
        panel.beginSheetModal(for:window){result in completionHandler(result == .OK ? panel.url:nil)}
    }
    func downloadDidFinish(_ download:WKDownload){downloads.removeAll{$0 === download}}
    func download(_ download:WKDownload,didFailWithError error:Error,resumeData:Data?){downloads.removeAll{$0 === download}}
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool{return false}
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows:Bool)->Bool{showWindow();return true}
    func applicationWillTerminate(_ notification:Notification){isQuitting=true;server?.terminationHandler=nil;if let server,server.isRunning {server.terminate();let end=Date().addingTimeInterval(4);while server.isRunning && Date()<end {Thread.sleep(forTimeInterval:0.05)};if server.isRunning {kill(server.processIdentifier,SIGKILL)}};if lockFD>=0{flock(lockFD,LOCK_UN);close(lockFD)}}
    func fail(_ title:String,_ detail:String){let a=NSAlert();a.messageText=title;a.informativeText=detail;a.alertStyle = .warning;a.runModal();NSApp.terminate(nil)}
}
let application=NSApplication.shared
application.setActivationPolicy(.regular)
let delegate=AppDelegate();application.delegate=delegate
application.run()

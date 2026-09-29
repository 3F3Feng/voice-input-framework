import SwiftUI

@main
struct VoiceInputApp: App {
    @StateObject private var controller = DictationController.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(controller)
                // 键盘点麦克风时拉起应用:voiceinput://dictate?id=…
                .onOpenURL { controller.handle(url: $0) }
        }
    }
}

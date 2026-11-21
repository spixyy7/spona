package app.spona;

import android.content.ClipData;
import android.os.Binder;
import android.os.Build;
import android.os.IBinder;
import android.os.IInterface;
import android.os.Parcel;
import android.os.PersistableBundle;
import android.util.Base64;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;

public final class ClipHelper {
    private static final String CALLER = "com.android.shell";
    private static final String LISTENER = "android.content.IOnPrimaryClipChangedListener";
    private static final String SUPPRESS_OVERLAY = "com.android.systemui.SUPPRESS_CLIPBOARD_OVERLAY";
    private static final long POLL_MILLIS = 700;
    private static final Object LOCK = new Object();
    private static final PrintStream OUT = System.out;

    private static Object clipboard;
    private static String last;
    private static Binder listenerBinder;

    private ClipHelper() {
    }

    public static void main(String[] args) throws Exception {
        IBinder service = (IBinder) Class.forName("android.os.ServiceManager")
                .getMethod("getService", String.class)
                .invoke(null, "clipboard");
        clipboard = Class.forName("android.content.IClipboard$Stub")
                .getMethod("asInterface", IBinder.class)
                .invoke(null, service);
        try {
            synchronized (LOCK) {
                last = read();
            }
        } catch (Throwable error) {
            emit("E " + describe(error));
        }
        boolean listening = listen();
        if (!listening) {
            startPolling();
        }
        emit("R " + (listening ? "listen" : "poll") + " " + Build.VERSION.SDK_INT);
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = input.readLine()) != null) {
            if (line.startsWith("S ")) {
                try {
                    write(decode(line.substring(2)));
                    emit("A");
                } catch (Throwable error) {
                    emit("E " + describe(error));
                }
            } else if (line.equals("Q")) {
                break;
            }
        }
        System.exit(0);
    }

    private static Method find(String name) throws NoSuchMethodException {
        for (Method method : clipboard.getClass().getMethods()) {
            if (method.getName().equals(name)) {
                return method;
            }
        }
        throw new NoSuchMethodException(name);
    }

    private static Object[] arguments(Method method, Object subject) {
        Class<?>[] types = method.getParameterTypes();
        Object[] values = new Object[types.length];
        int strings = 0;
        for (int index = 0; index < types.length; index++) {
            Class<?> type = types[index];
            if (type == String.class) {
                values[index] = strings++ == 0 ? CALLER : null;
            } else if (type == int.class) {
                values[index] = 0;
            } else if (type == boolean.class) {
                values[index] = false;
            } else {
                values[index] = subject;
            }
        }
        return values;
    }

    private static String read() throws Exception {
        Method method = find("getPrimaryClip");
        ClipData clip = (ClipData) method.invoke(clipboard, arguments(method, null));
        if (clip == null || clip.getItemCount() == 0) {
            return null;
        }
        CharSequence text = clip.getItemAt(0).getText();
        return text == null ? null : text.toString();
    }

    private static void write(String text) throws Exception {
        ClipData clip = ClipData.newPlainText("Spona", text);
        if (Build.VERSION.SDK_INT >= 31) {
            PersistableBundle extras = new PersistableBundle();
            extras.putBoolean(SUPPRESS_OVERLAY, true);
            clip.getDescription().setExtras(extras);
        }
        Method method = find("setPrimaryClip");
        synchronized (LOCK) {
            last = text;
        }
        method.invoke(clipboard, arguments(method, clip));
    }

    private static void changed() {
        String text;
        try {
            text = read();
        } catch (Throwable error) {
            emit("E " + describe(error));
            return;
        }
        synchronized (LOCK) {
            if (text == null ? last == null : text.equals(last)) {
                return;
            }
            last = text;
        }
        if (text != null && !text.isEmpty()) {
            emit("C " + encode(text));
        }
    }

    private static boolean listen() {
        try {
            final Class<?> type = Class.forName(LISTENER);
            listenerBinder = new Binder() {
                @Override
                protected boolean onTransact(int code, Parcel data, Parcel reply, int flags) {
                    if (code == IBinder.FIRST_CALL_TRANSACTION) {
                        changed();
                        return true;
                    }
                    try {
                        return super.onTransact(code, data, reply, flags);
                    } catch (Exception error) {
                        return false;
                    }
                }
            };
            Object listener = Proxy.newProxyInstance(
                    type.getClassLoader(),
                    new Class<?>[] {type},
                    new InvocationHandler() {
                        @Override
                        public Object invoke(Object proxy, Method method, Object[] args) {
                            String name = method.getName();
                            if (name.equals("asBinder")) {
                                return listenerBinder;
                            }
                            if (name.equals("dispatchPrimaryClipChanged")) {
                                changed();
                                return null;
                            }
                            if (name.equals("hashCode")) {
                                return System.identityHashCode(proxy);
                            }
                            if (name.equals("equals")) {
                                return proxy == args[0];
                            }
                            if (name.equals("toString")) {
                                return "SponaClipListener";
                            }
                            return null;
                        }
                    });
            listenerBinder.attachInterface((IInterface) listener, LISTENER);
            Method method = find("addPrimaryClipChangedListener");
            method.invoke(clipboard, arguments(method, listener));
            return true;
        } catch (Throwable error) {
            emit("E listener: " + describe(error));
            return false;
        }
    }

    private static void startPolling() {
        Thread thread = new Thread(new Runnable() {
            @Override
            public void run() {
                while (true) {
                    changed();
                    try {
                        Thread.sleep(POLL_MILLIS);
                    } catch (InterruptedException error) {
                        return;
                    }
                }
            }
        });
        thread.setDaemon(true);
        thread.start();
    }

    private static String encode(String text) {
        return Base64.encodeToString(text.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
    }

    private static String decode(String text) {
        return new String(Base64.decode(text.trim(), Base64.DEFAULT), StandardCharsets.UTF_8);
    }

    private static String describe(Throwable error) {
        while (error instanceof InvocationTargetException && error.getCause() != null) {
            error = error.getCause();
        }
        String message = error.getMessage();
        String text = error.getClass().getSimpleName() + (message == null ? "" : ": " + message);
        return text.replace('\n', ' ').replace('\r', ' ');
    }

    private static void emit(String line) {
        synchronized (OUT) {
            OUT.println(line);
            OUT.flush();
        }
    }
}

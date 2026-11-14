package space.quest3.scanner;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.camera2.*;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.ImageReader;
import android.os.*;
import android.util.Base64;
import android.util.Size;
import android.view.*;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** A normal Horizon OS panel app: raw Camera2 input, durable spool, PC preview. */
public final class MainActivity extends Activity {
    private static final String HEADSET="horizonos.permission.HEADSET_CAMERA";
    private final Handler ui=new Handler(Looper.getMainLooper());
    private final ScheduledExecutorService network=Executors.newSingleThreadScheduledExecutor();
    private final Object spoolLock=new Object();
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private CameraManager manager;
    private CameraDevice camera;
    private CameraCaptureSession cameraSession;
    private ImageReader reader;
    private CameraCharacteristics characteristics;
    private final List<String> cameraIds=new ArrayList<>();
    private final Map<Long,TotalCaptureResult> captureResults=new ConcurrentHashMap<>();
    private ImageView preview;
    private WebView web;
    private TextView status,diagnostics;
    private EditText address,token,sizeX,sizeY,sizeZ;
    private Spinner selector;
    private SharedPreferences prefs;
    private volatile String base="",pairing="",sessionId="";
    private volatile boolean scanning=false,finishing=false,connected=false,resumed=false;
    private volatile String cameraMessage="相机未打开",networkMessage="尚未连接 PC";
    private long lastFrame=0;
    private int uploaded=0,width=1280,height=960;
    private File spoolRoot;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        prefs=getSharedPreferences("scanner",MODE_PRIVATE);
        spoolRoot=new File(getFilesDir(),"pending");spoolRoot.mkdirs();
        base=prefs.getString("server","");pairing=prefs.getString("token","");
        sessionId=prefs.getString("session","");finishing=prefs.getBoolean("finishing",false);
        buildUi();
        manager=(CameraManager)getSystemService(Context.CAMERA_SERVICE);
        network.scheduleWithFixedDelay(()->uploadTick(),1,1,TimeUnit.SECONDS);
        ui.post(new Runnable(){public void run(){
            status.setText(cameraMessage+"\n"+networkMessage+"\n"+(scanning?"正在采集":"采集已暂停")+" · 本地待传 "+pendingCount()+" 张 · 本次已传 "+uploaded);
            ui.postDelayed(this,1000);
        }});
    }

    private EditText field(LinearLayout row,String hint,String value,int weight) {
        EditText e=new EditText(this);e.setSingleLine(true);e.setHint(hint);e.setText(value);
        row.addView(e,new LinearLayout.LayoutParams(0,52,weight));return e;
    }
    private Button button(LinearLayout row,String title,Runnable action) {
        Button b=new Button(this);b.setText(title);row.addView(b);b.setOnClickListener(v->action.run());return b;
    }
    private LinearLayout row(LinearLayout parent) {
        LinearLayout r=new LinearLayout(this);r.setOrientation(LinearLayout.HORIZONTAL);parent.addView(r);return r;
    }
    private void buildUi() {
        LinearLayout root=new LinearLayout(this);root.setPadding(18,12,18,10);root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xff111a24);setContentView(root);
        TextView title=new TextView(this);title.setText("Quest3Space  ·  物件扫描 0.1");title.setTextSize(22);root.addView(title);
        LinearLayout connection=row(root);
        address=field(connection,"PC 地址，例如 192.168.1.10",base,3);
        token=field(connection,"PC 配对码",pairing,2);
        button(connection,"连接 PC",()->connect());
        LinearLayout cameras=row(root);
        selector=new Spinner(this);cameras.addView(selector,new LinearLayout.LayoutParams(0,50,1));
        button(cameras,"打开相机 / 重试",()->requestCamera());
        button(cameras,"查看模型",()->showModel());
        button(cameras,"查看相机",()->{web.setVisibility(View.GONE);preview.setVisibility(View.VISIBLE);});
        LinearLayout controls=row(root);
        sizeX=field(controls,"宽 mm","130",1);sizeY=field(controls,"深 mm","160",1);sizeZ=field(controls,"高 mm","200",1);
        sizeX.setInputType(2);sizeY.setInputType(2);sizeZ.setInputType(2);
        button(controls,"开始 / 继续",()->startScan());
        button(controls,"暂停",()->{scanning=false;networkMessage="采集暂停，已有数据继续上传";});
        button(controls,"完成并重建",()->finishScan());
        status=new TextView(this);status.setTextSize(15);root.addView(status);
        diagnostics=new TextView(this);diagnostics.setTextSize(12);root.addView(diagnostics);
        TextView guide=new TextView(this);guide.setText("物件保持静止，放在 A4 定位板中央。每个角度至少看到两个标记。缓慢移动并停顿；PC 页面可打印定位板。原图收到确认前保存在头显。首次连接需要相机授权。");root.addView(guide);
        FrameLayout viewport=new FrameLayout(this);root.addView(viewport,new LinearLayout.LayoutParams(-1,0,1));
        preview=new ImageView(this);preview.setScaleType(ImageView.ScaleType.FIT_CENTER);viewport.addView(preview,new FrameLayout.LayoutParams(-1,-1));
        web=new WebView(this);web.getSettings().setJavaScriptEnabled(true);web.getSettings().setDomStorageEnabled(true);
        web.setWebViewClient(new WebViewClient());web.setVisibility(View.GONE);viewport.addView(web,new FrameLayout.LayoutParams(-1,-1));
    }

    private void say(String message){networkMessage=message;}
    private void connect() {
        try {
            String text=address.getText().toString().trim();if(!text.contains("://"))text="http://"+text;
            URL u=new URL(text);if(!u.getProtocol().equals("http")&&!u.getProtocol().equals("https"))throw new Exception("只支持 HTTP / HTTPS 地址");
            if(u.getHost().isEmpty()||u.getUserInfo()!=null)throw new Exception("PC 地址无效");
            String next=new URL(u.getProtocol(),u.getHost(),u.getPort()<0?8765:u.getPort(),"").toString();
            String nextToken=token.getText().toString().trim().toUpperCase(Locale.ROOT);
            if(pendingCount()>0&&(!next.equals(base)||!nextToken.equals(pairing)))throw new Exception("有待传数据，请先连接原 PC 完成上传");
            base=next;pairing=nextToken;connected=false;
            prefs.edit().putString("server",base).putString("token",pairing).apply();
            network.execute(()->{try{
                JSONObject health=request("GET","/api/health",null,null);
                if(!"Quest3Space".equals(health.optString("name")))throw new IOException("该地址不是 Quest3Space 服务");
                JSONArray sessions=new JSONArray(requestText("GET","/api/sessions",null,null));
                String active="";
                for(int i=0;i<sessions.length();i++) {JSONObject s=sessions.getJSONObject(i);if(!s.optBoolean("archived")&&"scanning".equals(s.optString("status")))active=s.getString("id");}
                if(pendingCount()==0){sessionId=active;prefs.edit().putString("session",active).apply();}
                connected=true;say("PC 已连接。"+(sessionId.isEmpty()?"可以开始新扫描":"可继续扫描 "+sessionId));
            }catch(Exception e){say("连接失败："+e.getMessage());}});
        }catch(Exception e){say(e.getMessage());}
    }

    private void startScan() {
        if(!connected){say("请先连接 PC");return;}if(reader==null){say("请先打开相机并授权");return;}
        if(finishing){say("上一扫描正在完成，请等待");return;}
        final JSONArray dimensions=new JSONArray();
        try{for(EditText e:new EditText[]{sizeX,sizeY,sizeZ}){int value=Integer.parseInt(e.getText().toString());if(value<30||value>600)throw new Exception();dimensions.put(value);}}
        catch(Exception e){say("每个扫描尺寸须为 30–600 mm");return;}
        network.execute(()->{try{
            if(!sessionId.isEmpty()) {
                JSONObject old=request("GET","/api/sessions/"+sessionId,null,null);
                if(!"scanning".equals(old.getString("status")))sessionId="";
            }
            if(sessionId.isEmpty()) {
                JSONObject body=new JSONObject();body.put("size_mm",dimensions);body.put("resolution",80);
                sessionId=request("POST","/api/sessions",body.toString().getBytes(StandardCharsets.UTF_8),null).getString("id");
            }
            prefs.edit().putString("session",sessionId).apply();scanning=true;say("正在采集到扫描 "+sessionId);
        }catch(Exception e){say("开始失败："+e.getMessage());}});
    }
    private void finishScan(){if(sessionId.isEmpty()){say("没有活动扫描");return;}scanning=false;finishing=true;prefs.edit().putBoolean("finishing",true).apply();say("等待本地数据上传后完成扫描…");}
    private void showModel(){if(base.isEmpty()){say("先输入并连接 PC 地址");return;}web.loadUrl(base+"/#token="+pairing);preview.setVisibility(View.GONE);web.setVisibility(View.VISIBLE);}

    @Override protected void onResume(){super.onResume();resumed=true;cameraThread=new HandlerThread("camera");cameraThread.start();cameraHandler=new Handler(cameraThread.getLooper());requestCamera();}
    @Override protected void onPause(){scanning=false;resumed=false;closeCamera();if(cameraThread!=null){cameraThread.quitSafely();cameraThread=null;}super.onPause();}
    @Override protected void onDestroy(){network.shutdownNow();ui.removeCallbacksAndMessages(null);web.destroy();super.onDestroy();}
    private void requestCamera(){
        if(!resumed)return;
        ArrayList<String> missing=new ArrayList<>();
        if(checkSelfPermission(Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED)missing.add(Manifest.permission.CAMERA);
        // CAMERA also grants passthrough access on current Horizon OS. Request
        // HEADSET_CAMERA when declared by the OS, without treating its absence as fatal.
        try{getPackageManager().getPermissionInfo(HEADSET,0);if(checkSelfPermission(HEADSET)!=PackageManager.PERMISSION_GRANTED)missing.add(HEADSET);}catch(PackageManager.NameNotFoundException ignored){}
        if(!missing.isEmpty()){requestPermissions(missing.toArray(new String[0]),7);return;}
        enumerateAndOpen();
    }
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grants){super.onRequestPermissionsResult(code,permissions,grants);if(code==7){if(checkSelfPermission(Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED)enumerateAndOpen();else cameraMessage="相机权限未授予，请在系统设置授权后重试";}}
    private Integer vendor(CameraCharacteristics c,String key){try{return c.get(new CameraCharacteristics.Key<Integer>(key,Integer.class));}catch(Exception e){return null;}}
    private void enumerateAndOpen(){
        try{
            if(cameraIds.isEmpty()){
                List<String> labels=new ArrayList<>();
                for(String id:manager.getCameraIdList()){
                    CameraCharacteristics c=manager.getCameraCharacteristics(id);
                    Integer source=vendor(c,"com.meta.extra_metadata.camera_source"),position=vendor(c,"com.meta.extra_metadata.position");
                    cameraIds.add(id);labels.add("相机 "+id+" · "+(source!=null&&source==0?"透视 "+(position!=null&&position==0?"左":"右"):"其他 / 未标注"));
                }
                selector.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,labels));
                int preferred=0;for(int i=0;i<cameraIds.size();i++){CameraCharacteristics c=manager.getCameraCharacteristics(cameraIds.get(i));Integer source=vendor(c,"com.meta.extra_metadata.camera_source"),pos=vendor(c,"com.meta.extra_metadata.position");if(source!=null&&source==0){preferred=i;if(pos!=null&&pos==0)break;}}selector.setSelection(preferred);
            }
            if(cameraIds.isEmpty())throw new IOException("系统没有可访问相机。请确认 Quest 3 系统支持 Camera API 并开启透视");
            closeCamera();String id=cameraIds.get(Math.max(0,selector.getSelectedItemPosition()));
            characteristics=manager.getCameraCharacteristics(id);
            StreamConfigurationMap map=characteristics.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size[] sizes=map==null?null:map.getOutputSizes(ImageFormat.YUV_420_888);
            if(sizes==null||sizes.length==0)throw new IOException("相机无 YUV 输出，请换另一个相机");
            Size picked=null;for(Size s:sizes)if(s.getWidth()==1280&&s.getHeight()==960)picked=s;
            if(picked==null){for(Size s:sizes)if(s.getWidth()<=1280&&s.getHeight()<=1280&&(picked==null||s.getWidth()*s.getHeight()>picked.getWidth()*picked.getHeight()))picked=s;}
            if(picked==null)throw new IOException("没有支持的 ≤1280px 输出尺寸");width=picked.getWidth();height=picked.getHeight();
            reader=ImageReader.newInstance(width,height,ImageFormat.YUV_420_888,3);
            reader.setOnImageAvailableListener(r->onImage(r),cameraHandler);
            cameraMessage="正在打开相机 "+id+" · "+width+"×"+height;
            manager.openCamera(id,new CameraDevice.StateCallback(){
                @Override public void onOpened(CameraDevice c){if(!resumed){c.close();return;}camera=c;createSession();}
                @Override public void onDisconnected(CameraDevice c){c.close();camera=null;scanning=false;cameraMessage="相机断开，请重试";}
                @Override public void onError(CameraDevice c,int error){c.close();camera=null;scanning=false;cameraMessage="相机错误 "+error+"，请换相机或检查系统权限";}
            },cameraHandler);
        }catch(Exception e){cameraMessage="打开失败："+e.getMessage();}
    }
    private void createSession(){try{
        camera.createCaptureSession(Collections.singletonList(reader.getSurface()),new CameraCaptureSession.StateCallback(){
            @Override public void onConfigured(CameraCaptureSession s){if(camera==null||reader==null){s.close();return;}cameraSession=s;try{
                CaptureRequest.Builder b=camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);b.addTarget(reader.getSurface());b.set(CaptureRequest.CONTROL_MODE,CaptureRequest.CONTROL_MODE_AUTO);
                s.setRepeatingRequest(b.build(),new CameraCaptureSession.CaptureCallback(){@Override public void onCaptureCompleted(CameraCaptureSession cs,CaptureRequest cr,TotalCaptureResult result){Long ts=result.get(CaptureResult.SENSOR_TIMESTAMP);if(ts!=null){captureResults.put(ts,result);if(captureResults.size()>20)captureResults.clear();}}},cameraHandler);
                cameraMessage="相机运行 · "+width+"×"+height;
            }catch(Exception e){cameraMessage="启动采集失败："+e.getMessage();}}
            @Override public void onConfigureFailed(CameraCaptureSession s){cameraMessage="相机配置失败，请换相机或重新授权";}
        },cameraHandler);
    }catch(Exception e){cameraMessage=e.getMessage();}}
    private void closeCamera(){if(cameraSession!=null){cameraSession.close();cameraSession=null;}if(camera!=null){camera.close();camera=null;}if(reader!=null){reader.close();reader=null;}captureResults.clear();}

    private JSONObject metadata(Image image) throws Exception {
        TotalCaptureResult result=captureResults.remove(image.getTimestamp());
        float[] intrinsic=characteristics.get(CameraCharacteristics.LENS_INTRINSIC_CALIBRATION);
        Rect active=characteristics.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE);
        Rect crop=result==null?null:result.get(CaptureResult.SCALER_CROP_REGION);if(crop==null)crop=active;
        if(crop==null)throw new IOException("相机没有有效像素范围，无法可靠标定");
        double fx,fy,cx,cy;
        String calibrationSource="LENS_INTRINSIC_CALIBRATION";
        if(intrinsic!=null&&intrinsic.length>=4&&intrinsic[0]>0&&intrinsic[1]>0){fx=intrinsic[0];fy=intrinsic[1];cx=intrinsic[2];cy=intrinsic[3];}
        else{
            float[] focal=characteristics.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS);
            android.util.SizeF physical=characteristics.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE);
            Size pixel=characteristics.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE);
            if(focal==null||focal.length==0||physical==null||pixel==null)throw new IOException("相机没有内参；更换透视相机后重试");
            fx=focal[0]*pixel.getWidth()/physical.getWidth();fy=focal[0]*pixel.getHeight()/physical.getHeight();cx=pixel.getWidth()/2.0;cy=pixel.getHeight()/2.0;calibrationSource="physical-sensor estimate";
        }
        double cw=crop.width(),ch=crop.height(),x=crop.left,y=crop.top,aspect=(double)image.getWidth()/image.getHeight();
        if(cw/ch>aspect){double effective=ch*aspect;x+=(cw-effective)/2;cw=effective;}else{double effective=cw/aspect;y+=(ch-effective)/2;ch=effective;}
        double sx=image.getWidth()/cw,sy=image.getHeight()/ch;
        JSONArray k=new JSONArray();k.put(fx*sx);k.put(fy*sy);k.put((cx-x)*sx);k.put((cy-y)*sy);
        JSONObject m=new JSONObject();m.put("protocol",1);m.put("timestamp_ns",Long.toString(image.getTimestamp()));m.put("width",image.getWidth());m.put("height",image.getHeight());m.put("intrinsics",k);m.put("calibration_source",calibrationSource);m.put("pose_source","PC printed-board solvePnP");m.put("camera_id",camera==null?"unknown":camera.getId());
        m.put("crop_region",new JSONArray(new int[]{crop.left,crop.top,crop.right,crop.bottom}));m.put("capture_result_matched",result!=null);m.put("device",Build.MANUFACTURER+" "+Build.MODEL);m.put("os",Build.VERSION.RELEASE);
        return m;
    }
    private static byte[] jpeg(Image image) throws IOException {
        int w=image.getWidth(),h=image.getHeight();byte[] nv21=new byte[w*h*3/2];
        Image.Plane[] planes=image.getPlanes();
        for(int p=0;p<3;p++){
            ByteBuffer b=planes[p].getBuffer();int rows=p==0?h:h/2,cols=p==0?w:w/2,rowStride=planes[p].getRowStride(),pixelStride=planes[p].getPixelStride(),start=b.position();
            for(int y=0;y<rows;y++)for(int x=0;x<cols;x++){
                int dest=p==0?y*w+x:w*h+y*w+x*2+(p==1?1:0);
                nv21[dest]=b.get(start+y*rowStride+x*pixelStride);
            }
        }
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        if(!new YuvImage(nv21,ImageFormat.NV21,w,h,null).compressToJpeg(new Rect(0,0,w,h),90,out))throw new IOException("JPEG 编码失败");return out.toByteArray();
    }
    private void onImage(ImageReader source){
        Image image=null;try{
            image=source.acquireLatestImage();if(image==null)return;
            long now=SystemClock.elapsedRealtime();if(now-lastFrame<700)return;lastFrame=now;
            byte[] data=jpeg(image);
            final Bitmap bitmap=BitmapFactory.decodeByteArray(data,0,data.length);
            ui.post(()->preview.setImageBitmap(bitmap));
            if(!scanning||sessionId.isEmpty())return;
            if(pendingCount()>=180||getFilesDir().getUsableSpace()<256L*1024*1024){scanning=false;say("本地缓存接近上限，采集暂停；等待上传或释放头显空间");return;}
            JSONObject meta=metadata(image);
            String sid=sessionId;File dir=new File(spoolRoot,sid);dir.mkdirs();String fid=Long.toString(image.getTimestamp());
            synchronized(spoolLock){write(new File(dir,fid+".json"),meta.toString().getBytes(StandardCharsets.UTF_8));File temp=new File(dir,fid+".tmp");write(temp,data);if(!temp.renameTo(new File(dir,fid+".jpg")))throw new IOException("无法保存采集图像");}
            ui.post(()->diagnostics.setText("内参 "+meta.optJSONArray("intrinsics")+" · "+meta.optString("calibration_source")));
        }catch(Exception e){cameraMessage="采集错误："+e.getMessage();}finally{if(image!=null)image.close();}
    }
    private static void write(File path,byte[] bytes)throws IOException{try(FileOutputStream out=new FileOutputStream(path)){out.write(bytes);out.getFD().sync();}}
    private int pendingCount(){int n=0;File[] dirs=spoolRoot.listFiles();if(dirs!=null)for(File d:dirs){File[] files=d.listFiles((f,name)->name.endsWith(".jpg"));if(files!=null)n+=files.length;}return n;}
    private static byte[] read(File path)throws IOException{try(FileInputStream in=new FileInputStream(path);ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1)out.write(b,0,n);return out.toByteArray();}}
    private void uploadTick(){
        if(base.isEmpty()||pairing.isEmpty())return;
        try{
            File[] dirs=spoolRoot.listFiles();if(dirs!=null)for(File dir:dirs){
                File[] frames=dir.listFiles((f,name)->name.endsWith(".jpg"));if(frames==null)continue;Arrays.sort(frames);
                int count=0;for(File file:frames){if(count++>=4)break;
                    String fid=file.getName().replace(".jpg","");File meta=new File(dir,fid+".json");
                    JSONObject receipt=request("POST","/api/sessions/"+dir.getName()+"/frames/"+fid,read(file),read(meta));
                    if(!receipt.optBoolean("stored"))throw new IOException("PC 未确认保存");
                    synchronized(spoolLock){if(!file.delete())throw new IOException("无法删除已传缓存");meta.delete();}uploaded++;
                }
            }
            if(finishing&&pendingCount()==0&&!sessionId.isEmpty()){
                JSONObject result=request("POST","/api/sessions/"+sessionId+"/finish",new byte[0],null);
                String status=result.optString("status");
                if("complete".equals(status)||"failed".equals(status)){finishing=false;prefs.edit().putBoolean("finishing",false).apply();say("complete".equals(status)?"模型已导出；点击查看模型":"重建失败："+result.optString("hint"));sessionId="";prefs.edit().putString("session","").apply();}
                else say("PC 正在完成重建，请稍候…");
            }else if(connected&&!sessionId.isEmpty()){
                JSONObject result=request("GET","/api/sessions/"+sessionId,null,null);say(result.optString("hint")+" · 有效 "+result.optInt("accepted")+" 张");
            }
        }catch(Exception e){say("传输 / PC："+e.getMessage()+"（本地图像保留，将重试）");}
    }
    private JSONObject request(String method,String path,byte[] body,byte[] meta)throws Exception{return new JSONObject(requestText(method,path,body,meta));}
    private String requestText(String method,String path,byte[] body,byte[] meta)throws Exception{
        HttpURLConnection c=(HttpURLConnection)new URL(base+path).openConnection();
        c.setConnectTimeout(3500);c.setReadTimeout(12000);c.setRequestMethod(method);c.setRequestProperty("X-Scan-Token",pairing);
        if(meta!=null){c.setRequestProperty("Content-Type","image/jpeg");c.setRequestProperty("X-Frame-Meta",Base64.encodeToString(meta,Base64.NO_WRAP));}else c.setRequestProperty("Content-Type","application/json");
        try{
            if(body!=null){c.setDoOutput(true);c.setFixedLengthStreamingMode(body.length);try(OutputStream out=c.getOutputStream()){out.write(body);}}
            int code=c.getResponseCode();InputStream source=code<400?c.getInputStream():c.getErrorStream();ByteArrayOutputStream buffer=new ByteArrayOutputStream();
            if(source!=null)try(InputStream in=source){byte[] bytes=new byte[4096];int n;while((n=in.read(bytes))!=-1){buffer.write(bytes,0,n);if(buffer.size()>1024*1024)throw new IOException("响应过大");}}
            String text=new String(buffer.toByteArray(),StandardCharsets.UTF_8);if(code>=400)throw new IOException("HTTP "+code+" "+text);return text;
        }finally{c.disconnect();}
    }
}

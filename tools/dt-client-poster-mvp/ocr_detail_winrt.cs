using System;
using System.Text;
using System.Linq;
using System.Threading.Tasks;
using Windows.Globalization;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage;

internal static class OcrPreflight
{
    private static T Wait<T>(object operation)
    {
        var asTask = Type.GetType("System.WindowsRuntimeSystemExtensions, System.Runtime.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089", true)
            .GetMethods().Single(method => method.Name == "AsTask" && method.IsGenericMethod &&
               method.GetParameters().Length == 1 && method.GetParameters()[0].ParameterType.Name == "IAsyncOperation`1");
        return ((Task<T>)asTask.MakeGenericMethod(typeof(T)).Invoke(null, new[] { operation })).GetAwaiter().GetResult();
    }

    public static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        if (args.Length != 1)
        {
            Console.Error.WriteLine("Supply one synthetic PNG.");
            return 2;
        }
        try
        {
            var engine = OcrEngine.TryCreateFromLanguage(new Language("zh-Hans-CN"));
            if (engine == null)
            {
                Console.Error.WriteLine("OCR language unavailable: zh-Hans-CN");
                return 3;
            }
            var file = Wait<StorageFile>(StorageFile.GetFileFromPathAsync(System.IO.Path.GetFullPath(args[0])));
            using (var stream = Wait<Windows.Storage.Streams.IRandomAccessStreamWithContentType>(file.OpenReadAsync()))
            {
                var decoder = Wait<BitmapDecoder>(BitmapDecoder.CreateAsync(stream));
                using (var bitmap = Wait<SoftwareBitmap>(decoder.GetSoftwareBitmapAsync()))
                {
                    var result = Wait<OcrResult>(engine.RecognizeAsync(bitmap));
                    foreach (var line in result.Lines)
                        Console.WriteLine(line.Text);
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}



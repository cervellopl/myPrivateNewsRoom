# R8 rules for the release build.
#
# Retrofit and OkHttp ship their own consumer rules; these cover the parts R8
# cannot infer on its own - the kotlinx.serialization generated serializers and
# the data classes they serialize, which are only ever referenced reflectively.

-keepattributes *Annotation*, InnerClasses, Signature, RuntimeVisibleAnnotations, AnnotationDefault

# --- kotlinx.serialization -------------------------------------------------
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
# our @Serializable models and their generated serializers
-keep,includedescriptorclasses class pl.myprivatenewsroom.data.**$$serializer { *; }
-keepclassmembers class pl.myprivatenewsroom.data.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep class pl.myprivatenewsroom.data.** { *; }

# --- Retrofit --------------------------------------------------------------
# the API interface is implemented by a runtime proxy, so its signatures must stay
-keep,allowobfuscation interface pl.myprivatenewsroom.data.NewsApi { *; }
-keepattributes RuntimeVisibleParameterAnnotations
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn retrofit2.**

# --- Coroutines ------------------------------------------------------------
-dontwarn kotlinx.coroutines.**

# quieter build: these are compile-time only
-dontwarn org.jetbrains.annotations.**
-dontwarn javax.annotation.**

package com.oracle.svm.agent;

import java.io.File;
import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.FileVisitor;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.StringTokenizer;

public class Utils {

    public static void main(String[] args) {
// List<Path> metaInfNativeImageFolders = getMetaInfNativeImageFolders(
// "/Users/aclement/gits3/spring-graal-native/samples/commandlinerunner/target/classes:/Users/aclement/gits3/spring-graal-native/spring-native/target/classes:/Users/aclement/.m2/repository/org/springframework/boot/spring-boot-starter/2.4.3/spring-boot-starter-2.4.3.jar:/Users/aclement/.m2/repository/org/springframework/boot/spring-boot/2.4.3/spring-boot-2.4.3.jar:/Users/aclement/.m2/repository/org/springframework/spring-context/5.3.4/spring-context-5.3.4.jar:/Users/aclement/.m2/repository/org/springframework/spring-aop/5.3.4/spring-aop-5.3.4.jar:/Users/aclement/.m2/repository/org/springframework/spring-beans/5.3.4/spring-beans-5.3.4.jar:/Users/aclement/.m2/repository/org/springframework/spring-expression/5.3.4/spring-expression-5.3.4.jar:/Users/aclement/.m2/repository/org/springframework/boot/spring-boot-autoconfigure/2.4.3/spring-boot-autoconfigure-2.4.3.jar:/Users/aclement/.m2/repository/jakarta/annotation/jakarta.annotation-api/1.3.5/jakarta.annotation-api-1.3.5.jar:/Users/aclement/.m2/repository/org/springframework/spring-core/5.3.4/spring-core-5.3.4.jar:/Users/aclement/.m2/repository/org/springframework/spring-jcl/5.3.4/spring-jcl-5.3.4.jar:/Users/aclement/.m2/repository/org/yaml/snakeyaml/1.27/snakeyaml-1.27.jar:/Users/aclement/.m2/repository/net/bytebuddy/byte-buddy/1.10.20/byte-buddy-1.10.20.jar");
// System.out.println(metaInfNativeImageFolders);
    }

    public static List<Path> getMetaInfNativeImageFolders(String property) {

// System.out.println("Properties are " + System.getProperties());
// System.out.println("env is " + System.getenv());
// String property = System.getProperty("java.class.path");
// if (property == null) {
// property = System.getProperty("aaa");
// }
        System.out.println("Classpath is " + property);
        List<Path> metaIntNativeImageFolders = new ArrayList<>();
        if (property != null) {
            StringTokenizer st = new StringTokenizer(property, File.pathSeparator);
            while (st.hasMoreElements()) {
                String cpentry = st.nextToken();
                System.out.println("Searching " + cpentry);
                ClassCollectorFileVisitor ccfv = new ClassCollectorFileVisitor();
                try {
                    Files.walkFileTree(new File(cpentry).toPath(), ccfv);
                } catch (IOException e) {
                    e.printStackTrace();
                }
                metaIntNativeImageFolders.addAll(ccfv.getClassFiles());
            }
        }
        System.out.println("FILTER: discovered paths for META-INF/native-image are " + metaIntNativeImageFolders);
        return metaIntNativeImageFolders;
    }

    static class ClassCollectorFileVisitor implements FileVisitor<Path> {

        private final List<Path> collector = new ArrayList<>();

        public List<Path> getClassFiles() {
            return collector;
        }

        @Override
        public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) throws IOException {
            System.out.println("Checking " + dir.toString());
            if (dir.toString().contains("META-INF" + File.separatorChar + "native-image")) {
                collector.add(dir);
            }
            return FileVisitResult.CONTINUE;
        }

        @Override
        public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) throws IOException {
            return FileVisitResult.CONTINUE;
        }

        @Override
        public FileVisitResult visitFileFailed(Path file, IOException exc) throws IOException {
            return FileVisitResult.CONTINUE;
        }

        @Override
        public FileVisitResult postVisitDirectory(Path dir, IOException exc) throws IOException {
            return FileVisitResult.CONTINUE;
        }

    }

}

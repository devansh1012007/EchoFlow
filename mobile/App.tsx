import React from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { NavigationContainer, DarkTheme } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { Headphones, Compass, PlusCircle, Inbox, User } from 'lucide-react-native';

import { AuthProvider } from './src/context/AuthContext';
import { PlayerProvider } from './src/context/PlayerContext';
import { FeedScreen } from './src/screens/FeedScreen';
import { ExploreScreen } from './src/screens/ExploreScreen';
import { UploadScreen } from './src/screens/UploadScreen';
import { InboxScreen } from './src/screens/InboxScreen';
import { ProfileScreen } from './src/screens/ProfileScreen';

const Tab = createBottomTabNavigator();

const EchoFlowDarkTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    background: '#0A0A0A',
    card: '#0D0D0D',
    text: '#F5F5F5',
    border: 'rgba(255,255,255,0.08)',
    primary: '#FF6321',
  },
};

export default function App() {
  return (
    <SafeAreaProvider>
      <AuthProvider>
        <PlayerProvider>
          <SafeAreaView style={{ flex: 1, backgroundColor: '#0A0A0A' }}>
            <StatusBar style="light" />
            <NavigationContainer theme={EchoFlowDarkTheme}>
              <Tab.Navigator
                screenOptions={({ route }) => ({
                  headerShown: false,
                  tabBarStyle: {
                    backgroundColor: '#0D0D0D',
                    borderTopColor: 'rgba(255,255,255,0.08)',
                    borderTopWidth: 1,
                    height: 60,
                    paddingBottom: 8,
                    paddingTop: 8,
                  },
                  tabBarActiveTintColor: '#FF6321',
                  tabBarInactiveTintColor: 'rgba(255,255,255,0.35)',
                  tabBarLabelStyle: {
                    fontSize: 10,
                    fontWeight: '800',
                    textTransform: 'uppercase',
                  },
                  tabBarIcon: ({ color, size, focused }) => {
                    switch (route.name) {
                      case 'Feed':
                        return <Headphones size={22} color={color} />;
                      case 'Explore':
                        return <Compass size={22} color={color} />;
                      case 'Upload':
                        return <PlusCircle size={24} color={focused ? '#FF6321' : color} />;
                      case 'Inbox':
                        return <Inbox size={22} color={color} />;
                      case 'Profile':
                        return <User size={22} color={color} />;
                      default:
                        return null;
                    }
                  },
                })}
              >
                <Tab.Screen
                  name="Feed"
                  component={FeedScreen}
                  options={{ tabBarLabel: 'Feed' }}
                />
                <Tab.Screen
                  name="Explore"
                  component={ExploreScreen}
                  options={{ tabBarLabel: 'Discover' }}
                />
                <Tab.Screen
                  name="Upload"
                  component={UploadScreen}
                  options={{ tabBarLabel: 'Studio' }}
                />
                <Tab.Screen
                  name="Inbox"
                  component={InboxScreen}
                  options={{ tabBarLabel: 'Inbox' }}
                />
                <Tab.Screen
                  name="Profile"
                  component={ProfileScreen}
                  options={{ tabBarLabel: 'Profile' }}
                />
              </Tab.Navigator>
            </NavigationContainer>
          </SafeAreaView>
        </PlayerProvider>
      </AuthProvider>
    </SafeAreaProvider>
  );
}
